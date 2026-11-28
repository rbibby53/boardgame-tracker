"""Live game nights: start one, join / leave, bring games, vote on what's next, keep score live, end it.
The night page refreshes its live panel every few seconds (GET /live/{id}/panel), so everyone's phone stays
in step without websockets. Actions are plain form posts that come back to the night."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import live as L
from ..auth import can, current_user, require, verify_csrf
from ..db import get_db
from ..models import Favorite, Game, LiveNight, Play, PlaySeat, Player, Poll, PollOption, natural_key
from ..templating import flash, render

router = APIRouter(prefix="/live")
MAX_VOTE = 12


def _night(db: Session, night_id: int) -> LiveNight:
    night = L.get(db, night_id)
    if night is None:
        raise HTTPException(404, "Game night not found")
    return night


def _back(night: LiveNight, anchor: str = "") -> RedirectResponse:
    return RedirectResponse(f"/live/{night.id}{anchor}", status_code=303)


def _ctx(request: Request, db: Session, night: LiveNight, user: Player) -> dict:
    here_ids = {m.player_id for m in night.here}
    n_here = len(here_ids)
    pile = L.playable_pile(night)
    vote = L.open_vote(db, night)
    if vote is not None:
        vote = db.scalar(select(Poll).where(Poll.id == vote.id).options(
            selectinload(Poll.options).selectinload(PollOption.game),
            selectinload(Poll.options).selectinload(PollOption.votes)))
    played = _played_counts(night)
    recent = db.execute(select(PlaySeat.player_id, func.max(Play.played_at)).join(Play)
                        .group_by(PlaySeat.player_id)).all()
    last_seen = dict(recent)
    people = [p for p in db.scalars(select(Player).where(Player.is_active.is_(True))).all() if p.id not in here_ids]
    people.sort(key=lambda p: (-(last_seen[p.id].timestamp()) if p.id in last_seen else 0, p.display_name.lower()))
    favs = set(db.scalars(select(Favorite.game_id).where(Favorite.player_id == user.id)).all())
    in_pile = {pg.game_id for pg in night.pile}
    suggest = [g for g in db.scalars(select(Game).where(Game.id.in_(favs or {-1}), Game.archived.is_(False))).all()
               if g.id not in in_pile]
    suggest.sort(key=lambda g: natural_key(g.name))
    return {
        "night": night, "me_here": user.id in here_ids, "n_here": n_here, "pile": pile,
        "pile_rows": sorted(night.pile, key=lambda pg: natural_key(pg.game.name)),
        "fits": {g.id: L.fits(g, n_here) for g in pile}, "played": played,
        "vote": vote, "my_votes": {o.id for o in (vote.options if vote else []) for v in o.votes if v.player_id == user.id},
        "cur": L.current(night), "summary": L.summary_night(night),
        "people": people[:40], "suggest": suggest[:12],
        "all_games": sorted((g for g in db.scalars(select(Game).where(Game.archived.is_(False))).all()
                             if not (g.is_box and g.children)), key=lambda g: natural_key(g.name)),
        "is_host": night.host_id == user.id,
        "can_run": can(request, "log_plays") and user.id in here_ids,
    }


def _played_counts(night: LiveNight) -> dict[int, int]:
    out: dict[int, int] = {}
    for p in night.plays:
        out[p.game_id] = out.get(p.game_id, 0) + 1
    return out


# ---------------------------------------------------------------- start / view


@router.get("/new")
def new_night(request: Request, user: Player = Depends(require("log_plays")), db: Session = Depends(get_db)):
    mine = L.night_of_player(db, user)
    return render(request, "live/new.html", {"default_title": f"Game night at {user.display_name.split()[0]}'s",
                                             "already": mine, "open_nights": L.open_nights(db)})


@router.post("", dependencies=[Depends(verify_csrf)])
async def start_night(request: Request, user: Player = Depends(require("log_plays")), db: Session = Depends(get_db)):
    form = await request.form()
    title = (form.get("title") or "").strip()[:150] or f"Game night at {user.display_name.split()[0]}'s"
    night = LiveNight(title=title, location=(form.get("location") or "").strip()[:200] or None, host_id=user.id)
    db.add(night)
    db.flush()
    L.join(db, night, user)
    db.commit()
    flash(request, "Game night started. It's on everyone's dashboard: people tap Join when they arrive.")
    return _back(night)


@router.get("/{night_id}")
def show_night(night_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    night = _night(db, night_id)
    return render(request, "live/show.html", _ctx(request, db, night, user))


@router.get("/{night_id}/panel")
def panel(night_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    """The live part of the night page, re-fetched every few seconds."""
    night = _night(db, night_id)
    return render(request, "live/_panel.html", _ctx(request, db, night, user))


# ---------------------------------------------------------------- people


@router.post("/{night_id}/join", dependencies=[Depends(verify_csrf)])
def join(night_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    night = _night(db, night_id)
    if not night.is_open:
        flash(request, "That game night has ended.", "err")
        return _back(night)
    L.join(db, night, user)
    db.commit()
    return _back(night)


@router.post("/{night_id}/leave", dependencies=[Depends(verify_csrf)])
async def leave(night_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    night = _night(db, night_id)
    form = await request.form()
    pid = int(form.get("player_id") or user.id)
    if pid != user.id and not can(request, "log_plays"):
        raise HTTPException(403)
    L.leave(night, pid)
    db.commit()
    if pid == user.id:
        flash(request, f"You left {night.title}. Tap Join if you come back.")
        return RedirectResponse("/", status_code=303)
    return _back(night, "#people")


@router.post("/{night_id}/people", dependencies=[Depends(verify_csrf)])
async def add_person(night_id: int, request: Request, user: Player = Depends(require("log_plays")),
                     db: Session = Depends(get_db)):
    """Mark someone as here (they don't need the app), or add a walk-in guest by name."""
    night = _night(db, night_id)
    if not night.is_open:
        return _back(night)
    form = await request.form()
    guest = (form.get("guest") or "").strip()[:150]
    if guest:
        if not can(request, "manage_guests"):
            raise HTTPException(403)
        p = db.scalar(select(Player).where(func.lower(Player.display_name) == guest.lower(), Player.source == "local"))
        if p is None:
            p = Player(display_name=guest, source="local")
            db.add(p)
            db.flush()
    else:
        p = db.get(Player, int(form.get("player_id") or 0))
        if p is None:
            raise HTTPException(404)
    L.join(db, night, p)
    db.commit()
    return _back(night, "#people")


# ---------------------------------------------------------------- the game pile, up next, votes


@router.post("/{night_id}/pile", dependencies=[Depends(verify_csrf)])
async def add_game(night_id: int, request: Request, user: Player = Depends(require("log_plays")),
                   db: Session = Depends(get_db)):
    night = _night(db, night_id)
    form = await request.form()
    game = None
    if form.get("game_id"):
        game = db.get(Game, int(form.get("game_id")))
    elif (name := (form.get("name") or "").strip()):
        game = db.scalar(select(Game).where(func.lower(Game.name) == name.lower()))
    if game is None:
        flash(request, "Pick a game from the list.", "err")
    elif night.is_open:
        L.add_to_pile(night, game, user)
        db.commit()
    return _back(night, "#pile")


@router.post("/{night_id}/pile/{game_id}/remove", dependencies=[Depends(verify_csrf)])
def remove_game(night_id: int, game_id: int, request: Request, user: Player = Depends(require("log_plays")),
                db: Session = Depends(get_db)):
    night = _night(db, night_id)
    pg = next((g for g in night.pile if g.game_id == game_id), None)
    if pg is not None and night.is_open:
        night.pile.remove(pg)
        L.touch(night)
        db.commit()
    return _back(night, "#pile")


@router.post("/{night_id}/next", dependencies=[Depends(verify_csrf)])
async def set_next(night_id: int, request: Request, user: Player = Depends(require("log_plays")),
                   db: Session = Depends(get_db)):
    night = _night(db, night_id)
    form = await request.form()
    gid = form.get("game_id")
    if night.is_open:
        night.next_game_id = int(gid) if gid else None
        L.touch(night)
        db.commit()
    return _back(night)


@router.post("/{night_id}/vote", dependencies=[Depends(verify_csrf)])
def start_vote(night_id: int, request: Request, user: Player = Depends(require("start_votes")),
               db: Session = Depends(get_db)):
    """Vote on what's next: the pile games that fit everyone here."""
    night = _night(db, night_id)
    if not night.is_open:
        return _back(night)
    if L.open_vote(db, night):
        flash(request, "There's already a vote going.", "err")
        return _back(night, "#vote")
    n = len(night.here)
    options = [g for g in L.playable_pile(night) if L.fits(g, n)][:MAX_VOTE]
    if len(options) < 2:
        flash(request, f"Add at least two games that work for {n} to the pile first.", "err")
        return _back(night, "#pile")
    poll = Poll(title=f"What next? ({n} here)", created_by_id=user.id, live_night_id=night.id)
    for g in options:
        poll.options.append(PollOption(game_id=g.id))
    db.add(poll)
    L.touch(night)
    db.commit()
    return _back(night, "#vote")


# ---------------------------------------------------------------- the game being played


@router.post("/{night_id}/start", dependencies=[Depends(verify_csrf)])
async def start_game(night_id: int, request: Request, user: Player = Depends(require("log_plays")),
                     db: Session = Depends(get_db)):
    night = _night(db, night_id)
    if not night.is_open:
        return _back(night)
    form = await request.form()
    game = db.get(Game, int(form.get("game_id") or 0))
    if game is None or (game.is_box and game.children):
        flash(request, "Pick a game to start.", "err")
        return _back(night)
    if night.current_game_id:
        flash(request, f"{night.current_game.name} is still going. Save it or stop it first.", "err")
        return _back(night)
    if not L.is_here(night, user.id):
        L.join(db, night, user)
    if not any(pg.game_id == game.id for pg in night.pile):
        L.add_to_pile(night, game, user)
    L.start_game(night, game, user)
    db.commit()
    return RedirectResponse(f"/plays/new?night={night.id}", status_code=303)


@router.post("/{night_id}/takeover", dependencies=[Depends(verify_csrf)])
def take_over(night_id: int, request: Request, user: Player = Depends(require("log_plays")),
              db: Session = Depends(get_db)):
    night = _night(db, night_id)
    if night.is_open and night.current_game_id:
        if not L.is_here(night, user.id):
            L.join(db, night, user)
        L.take_over(night, user)
        db.commit()
        return RedirectResponse(f"/plays/new?night={night.id}", status_code=303)
    return _back(night)


@router.put("/{night_id}/current", dependencies=[Depends(verify_csrf)])
async def save_current(night_id: int, request: Request, user: Player = Depends(require("log_plays")),
                       db: Session = Depends(get_db)):
    """The scorekeeper's phone saves the game in progress here (JSON) so everyone sees it live."""
    night = _night(db, night_id)
    if not night.is_open or not night.current_game_id:
        return JSONResponse({"ok": False, "reason": "ended"}, 409)
    data = await request.json()
    ok, version = L.save_progress(night, user, int(data.get("version") or 0), data)
    try:  # the scorekeeper switched games in the form: show the right one to everyone
        gid = int(((data.get("state") or {}).get("game_id")) or (data.get("fields") or {}).get("game_id") or 0)
    except (TypeError, ValueError):
        gid = 0
    if ok and gid and gid != night.current_game_id and (g := db.get(Game, gid)) is not None:
        night.current_game_id, night.current_game = g.id, g
    if not ok:
        keeper = night.current_keeper.display_name if night.current_keeper else "Someone else"
        return JSONResponse({"ok": False, "reason": "taken", "keeper": keeper, "version": version}, 409)
    db.commit()
    return {"ok": True, "version": version}


@router.post("/{night_id}/current/stop", dependencies=[Depends(verify_csrf)])
def stop_game(night_id: int, request: Request, user: Player = Depends(require("log_plays")),
              db: Session = Depends(get_db)):
    night = _night(db, night_id)
    if night.current_game_id:
        name = night.current_game.name
        L.clear_current(night)
        db.commit()
        flash(request, f"Stopped {name} without saving it.")
    return _back(night)


# ---------------------------------------------------------------- end / edit


@router.post("/{night_id}/end", dependencies=[Depends(verify_csrf)])
def end_night(night_id: int, request: Request, user: Player = Depends(require("log_plays")),
              db: Session = Depends(get_db)):
    night = _night(db, night_id)
    # the host, anyone there, or an admin (not just anyone with edit rights, from across town)
    if not (night.host_id == user.id or L.is_here(night, user.id) or can(request, "admin")):
        raise HTTPException(403, "Only someone at the night can end it.")
    if night.is_open:
        L.end(night, "ended")
        for poll in db.scalars(select(Poll).where(Poll.live_night_id == night.id, Poll.closed_at.is_(None))).all():
            db.delete(poll)
        db.commit()
        flash(request, "Game night ended. Thanks for playing!")
    return _back(night)


@router.post("/{night_id}/edit", dependencies=[Depends(verify_csrf)])
async def edit_night(night_id: int, request: Request, user: Player = Depends(current_user),
                     db: Session = Depends(get_db)):
    night = _night(db, night_id)
    if not (night.host_id == user.id or can(request, "edit_nights")):
        raise HTTPException(403)
    form = await request.form()
    night.title = (form.get("title") or "").strip()[:150] or night.title
    night.location = (form.get("location") or "").strip()[:200] or None
    night.notes = (form.get("notes") or "").strip() or None
    db.commit()
    flash(request, "Saved.")
    return _back(night)
