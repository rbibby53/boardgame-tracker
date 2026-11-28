import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import media, notify
from ..auth import can, current_user, require, verify_csrf
from ..config import get_settings
from ..db import get_db
from ..models import (CategoryScore, Favorite, Game, GameExpansion, Play, PlaySeat, Player, Round, RoundScore,
                      natural_key, utcnow)
from ..scoring import PlayPayload, compute, play_scoring
from ..stats import color_for
from ..templating import flash, render

router = APIRouter(prefix="/plays")
PAGE = 40


def _get_play(db: Session, play_id: int) -> Play:
    play = db.scalar(
        select(Play)
        .where(Play.id == play_id)
        .options(
            selectinload(Play.game).selectinload(Game.categories),
            selectinload(Play.seats).selectinload(PlaySeat.player),
            selectinload(Play.rounds).selectinload(Round.scores),
            selectinload(Play.category_scores),
            selectinload(Play.photos),
        )
    )
    if play is None:
        raise HTTPException(404, "Play not found")
    return play


def can_edit(request: Request, user: Player, play: Play) -> bool:
    if can(request, "edit_all_plays"):
        return True
    return can(request, "log_plays") and (
        play.created_by_id == user.id or any(s.player_id == user.id for s in play.seats))


def _form_context(db: Session, user: Player, play: Play | None, state: dict, error: str | None = None) -> dict:
    games = db.scalars(select(Game).where(Game.archived.is_(False)).order_by(Game.name)
                       .options(selectinload(Game.categories), selectinload(Game.children),
                                selectinload(Game.expansions))).all()
    # A box of games (e.g. a Jackbox pack) isn't played itself: log the mini-game. Kept if this play already uses one.
    games = [g for g in games if not (g.is_box and g.children) or (play is not None and g.id == play.game_id)]
    if play is not None and play.game not in games:
        games = [play.game, *games]
    seated_ids = {s.player_id for s in play.seats} if play else set()
    players = db.scalars(
        select(Player).where((Player.is_active.is_(True)) | (Player.id.in_(seated_ids or {-1})))
        .order_by(Player.display_name)
    ).all()
    players = sorted(players, key=lambda p: p.id != user.id)

    last_lineup: dict[int, list[int]] = {}
    latest = db.execute(
        select(Play.game_id, func.max(Play.played_at)).group_by(Play.game_id)
    ).all()
    for gid, when in latest:
        lp = db.scalar(select(Play).where(Play.game_id == gid, Play.played_at == when)
                       .options(selectinload(Play.seats)))
        if lp:
            last_lineup[gid] = [s.player_id for s in lp.seats]

    # Remember how each game was scored last time (this person's last play of it first), and what they play most.
    last_mode: dict[int, str] = {}
    mine: dict[int, str] = {}
    recent_games: list[int] = []
    last_exp: dict[int, list[int]] = {}  # expansions this person played each game with last time
    for p in db.scalars(select(Play).options(selectinload(Play.played_with))
                        .order_by(Play.played_at.desc(), Play.id.desc()).limit(500)).all():
        mode = "wins" if p.scoring == "winloss" else "points"
        last_mode.setdefault(p.game_id, mode)
        if p.created_by_id == user.id:
            mine.setdefault(p.game_id, mode)
            last_exp.setdefault(p.game_id, [e.id for e in p.played_with if not e.archived])
            if p.game_id not in recent_games:
                recent_games.append(p.game_id)
    last_mode.update(mine)
    favs = set(db.scalars(select(Favorite.game_id).where(Favorite.player_id == user.id)).all())
    used_now = {e.id for e in play.played_with} if play is not None else set()
    played = dict(db.execute(select(PlaySeat.player_id, func.max(Play.played_at)).join(Play)
                             .group_by(PlaySeat.player_id)).all())

    locations = db.scalars(
        select(Play.location).where(Play.location.is_not(None)).group_by(Play.location)
        .order_by(func.count().desc()).limit(20)
    ).all()

    return {
        "play": play,
        "error": error,
        "games": games,
        "game_groups": _game_groups(games, recent_games[:5], favs),
        "locations": locations,
        "boot": {
            "games": [{"id": g.id, "name": g.name, "scoring": g.scoring, "uses_rounds": g.uses_rounds,
                       "round_label": g.round_label or "Round", "round_names": g.round_name_list,
                       "last_mode": last_mode.get(g.id, "points"),
                       "expansions": [{"id": e.id, "name": e.name, "owned": e.owned} for e in g.expansions
                                      if not e.archived or e.id in used_now],
                       "last_expansions": last_exp.get(g.id, []),
                       "categories": [{"id": c.id, "name": c.name, "archived": c.archived} for c in g.categories]}
                      for g in games],
            "players": [{"id": p.id, "name": p.display_name, "initials": p.initials, "color": color_for(p),
                         "avatar": p.avatar_url, "guest": p.source == "local",
                         "last": played[p.id].isoformat() if p.id in played else None} for p in players],
            "last_lineup": last_lineup,
            "state": state,
        },
    }


def _filter_games(db: Session) -> list[tuple[Game, bool]]:
    """Plays filter: every game, each box's mini-games / base game's variants right after it, as (game, nested)."""
    all_games = db.scalars(select(Game).options(selectinload(Game.children))).all()
    ids = {g.id for g in all_games}
    out: list[tuple[Game, bool]] = []
    for g in sorted(all_games, key=lambda g: natural_key(g.name)):
        if g.parent_id in ids:
            continue
        out.append((g, False))
        out.extend((c, True) for c in sorted(g.children, key=lambda c: natural_key(c.name)))
    return out


def _game_groups(games: list[Game], recent: list[int] | None = None,
                 favorites: set[int] | None = None) -> list[tuple[str | None, list[Game]]]:
    """For the game <select>: "Your favorites" and "Recently played" first, then standalone games as
    (None, [game]); a box's mini-games as (box name, [mini-games]); a game with variants as (name, [game, variants]).
    Name order; a group sorts by its head's name."""
    listed = {g.id: g for g in games}
    groups: dict[int, tuple[Game, list[Game]]] = {}
    out: list[tuple[Game | None, list[Game]]] = []
    for g in games:
        if g.parent is not None and not g.parent.archived and (g.in_box or g.parent_id in listed):
            head = g.parent
        elif not g.is_box and any(c.id in listed for c in g.children):
            head = g  # a base game heads its own group, with its variants
        else:
            out.append((None, [g]))
            continue
        if head.id not in groups:
            groups[head.id] = (head, [])
            out.append(groups[head.id])
        groups[head.id][1].append(g)
    for head, members in groups.values():
        members.sort(key=lambda m: (m.id != head.id, natural_key(m.name)))
    ordered = sorted(out, key=lambda t: natural_key((t[0] or t[1][0]).name))
    named: list[tuple[str | None, list[Game]]] = [(h.name if h else None, m) for h, m in ordered]
    top = [listed[gid] for gid in (recent or []) if gid in listed]
    favorites = favorites or set()
    fav = sorted((g for gid, g in listed.items()  # a starred box (Jackbox pack) brings its mini-games
                  if (gid in favorites or (g.in_box and g.parent_id in favorites)) and not (g.is_box and g.children)),
                 key=lambda g: natural_key(g.name))
    return (([("★ Your favorites", fav)] if fav else []) + ([("Recently played", top)] if len(top) >= 2 else [])
            + named)


def _state_from_play(play: Play) -> dict:
    return {
        "game_id": play.game_id,
        "players": [{"player_id": s.player_id, "score": s.score if s.score_manual else None,
                     "winner": s.is_winner, "team": s.team} for s in play.seats],
        "rounds": [{"label": r.label,
                    "scores": {str(rs.player_id): rs.score for rs in r.scores},
                    "won": [rs.player_id for rs in r.scores if rs.won]} for r in play.rounds],
        "categories": _sheet_state(play),
        "use_sheet": bool(play.category_scores),
        "winners_manual": play.winners_manual,
        "coop_won": play.coop_won,
        "use_rounds": bool(play.rounds),
        "mode": "wins" if play.scoring == "winloss" else "points",
        "expansions": [e.id for e in play.played_with],
    }


def _sheet_state(play: Play) -> dict:
    out: dict[str, dict[str, float]] = {}
    for cs in play.category_scores:
        out.setdefault(str(cs.category_id), {})[str(cs.player_id)] = cs.score
    return out


def _clean(v, maxlen: int | None = None):
    v = (v or "").strip()
    return (v[:maxlen] if maxlen else v) or None


async def _apply_form(request: Request, db: Session, play: Play, user: Player) -> tuple[Play | None, dict, str | None]:
    form = await request.form()
    raw_state: dict = {}
    try:
        raw_state = json.loads(form.get("payload") or "{}")
        payload = PlayPayload.model_validate(raw_state)
        game = db.get(Game, int(form.get("game_id") or 0))
        if game is None:
            raise ValueError("Pick a game.")
        played_at = datetime.fromisoformat(form.get("played_at") or "")
        dur = form.get("duration_min")
        duration = int(dur) if dur else None
        if duration is not None and not (0 < duration < 100000):
            raise ValueError("Duration must be a positive number of minutes.")
        for s in payload.players:
            if db.get(Player, s.player_id) is None:
                raise ValueError("Unknown player.")
        valid_cats = {c.id for c in game.categories}
        if set(payload.categories) - valid_cats:
            raise ValueError("The score sheet doesn't match this game. Reload and try again.")
        by_exp = {e.id: e for e in game.expansions}
        if set(payload.expansions) - set(by_exp):
            raise ValueError("Those expansions don't belong to this game. Reload and try again.")
    except ValidationError as e:
        msg = "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
        return None, {**raw_state, "game_id": form.get("game_id")}, msg
    except (ValueError, TypeError) as e:
        return None, {**raw_state, "game_id": form.get("game_id")}, str(e) or "Invalid input."

    scoring = play_scoring(game.scoring, payload.mode if payload.rounds else None)
    if scoring == "winloss":  # round wins only: no points anywhere
        for s in payload.players:
            s.score = None
        for r in payload.rounds:
            r.scores = {}
    results = compute(scoring, payload)

    play.game_id = game.id
    play.scoring = scoring if scoring != game.scoring else None
    play.played_with = sorted({by_exp[i] for i in payload.expansions}, key=lambda e: e.position)
    play.played_at = played_at.replace(second=0, microsecond=0, tzinfo=None)
    play.location = _clean(form.get("location"), 200)
    play.duration_min = duration
    play.expansions = _clean(form.get("expansions"), 500)
    play.notes = _clean(form.get("notes"))
    play.coop_won = bool(payload.coop_won) if game.scoring == "coop" else None
    play.winners_manual = payload.winners_manual and game.scoring != "coop"
    if play.created_by_id is None:
        play.created_by_id = user.id
    play.updated_at = utcnow()  # seats/rounds may change without touching the play row

    play.seats.clear()
    play.rounds.clear()
    play.category_scores.clear()
    db.flush()
    for r in results:
        play.seats.append(PlaySeat(player_id=r.player_id, score=r.score, score_manual=r.score_manual,
                                   place=r.place, is_winner=r.is_winner, team=r.team))
    for i, rin in enumerate(payload.rounds, 1):
        rnd = Round(number=i, label=rin.label)
        won = set(rin.won)
        for s in payload.players:
            score = rin.scores.get(s.player_id)
            if score is not None or s.player_id in won:
                rnd.scores.append(RoundScore(player_id=s.player_id, score=score, won=s.player_id in won))
        play.rounds.append(rnd)
    seated = {s.player_id for s in payload.players}
    for cat_id, scores in payload.categories.items():
        for pid, val in scores.items():
            if val is not None and pid in seated:
                play.category_scores.append(CategoryScore(category_id=cat_id, player_id=pid, score=val))
    return play, raw_state, None


@router.get("")
def list_plays(request: Request, game: int | None = None, player: int | None = None, page: int = 1,
               exact: bool = False, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Play).options(selectinload(Play.game), selectinload(Play.seats).selectinload(PlaySeat.player))
    if game:
        # a box's mini-games / a game's variants are included, unless exact (just that one game)
        g = db.get(Game, game)
        ids = [game, *(c.id for c in g.children)] if g is not None and not exact else [game]
        q = q.where(Play.game_id.in_(ids))
    if player:
        q = q.where(Play.seats.any(PlaySeat.player_id == player))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    page = max(page, 1)
    plays = db.scalars(q.order_by(Play.played_at.desc(), Play.id.desc()).offset((page - 1) * PAGE).limit(PAGE)).all()
    return render(request, "plays/list.html", {
        "plays": plays, "page": page, "pages": max(1, -(-total // PAGE)), "total": total,
        "games": _filter_games(db),
        "players": db.scalars(select(Player).order_by(Player.display_name)).all(),
        "f_game": game, "f_player": player, "f_exact": exact,
    })


@router.get("/new")
def new_play(request: Request, game: int | None = None, user: Player = Depends(require("log_plays")),
             db: Session = Depends(get_db)):
    state = {"game_id": game, "players": [{"player_id": user.id}], "rounds": [],
             "winners_manual": False, "coop_won": None}
    return render(request, "plays/form.html", {**_form_context(db, user, None, state),
                                               "now": datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y-%m-%dT%H:%M")})


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_play(request: Request, background: BackgroundTasks, user: Player = Depends(require("log_plays")),
                      db: Session = Depends(get_db)):
    play = Play()
    saved, state, error = await _apply_form(request, db, play, user)
    if not error:
        db.add(play)
    if error:
        db.rollback()
        form = await request.form()
        return render(request, "plays/form.html", {**_form_context(db, user, None, state, error),
                                                   "now": form.get("played_at"), "form": form}, 422)
    db.commit()
    s = get_settings()
    if s.notify_enabled:
        background.add_task(notify.send_logged, s, notify.summarize(_get_play(db, play.id), s.base_url))
    flash(request, "Play saved.")
    return RedirectResponse(f"/plays/{play.id}", status_code=303)


@router.get("/{play_id}")
def show_play(play_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    play = _get_play(db, play_id)
    seat_order = sorted(play.seats, key=lambda s: (s.place is None, s.place or 0, not s.is_winner))
    return render(request, "plays/show.html", {"play": play, "seats": seat_order, "can_edit": can_edit(request, user, play)})


@router.get("/{play_id}/edit")
def edit_play(play_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    play = _get_play(db, play_id)
    if not can_edit(request, user, play):
        raise HTTPException(403, "You can only edit plays you logged or played in (or your role doesn't allow editing).")
    return render(request, "plays/form.html", {**_form_context(db, user, play, _state_from_play(play)),
                                               "now": play.played_at.strftime("%Y-%m-%dT%H:%M")})


@router.post("/{play_id}", dependencies=[Depends(verify_csrf)])
async def update_play(play_id: int, request: Request, user: Player = Depends(current_user),
                      db: Session = Depends(get_db)):
    play = _get_play(db, play_id)
    if not can_edit(request, user, play):
        raise HTTPException(403)
    saved, state, error = await _apply_form(request, db, play, user)
    if error:
        db.rollback()
        play = _get_play(db, play_id)
        form = await request.form()
        return render(request, "plays/form.html", {**_form_context(db, user, play, state, error),
                                                   "now": form.get("played_at"), "form": form}, 422)
    db.commit()
    flash(request, "Play updated.")
    return RedirectResponse(f"/plays/{play.id}", status_code=303)


@router.post("/{play_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_play(play_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    play = _get_play(db, play_id)
    if not can_edit(request, user, play):
        raise HTTPException(403)
    keys = [ph.key for ph in play.photos]
    db.delete(play)
    db.commit()
    for k in keys:
        media.delete_photo(k)
    flash(request, "Play deleted.")
    return RedirectResponse("/plays", status_code=303)
