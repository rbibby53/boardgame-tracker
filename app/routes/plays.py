import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import media, notify
from ..auth import current_user, verify_csrf
from ..config import get_settings
from ..db import get_db
from ..models import CategoryScore, Game, Play, PlaySeat, Player, Round, RoundScore, utcnow
from ..scoring import PlayPayload, compute
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


def can_edit(user: Player, play: Play) -> bool:
    return user.is_admin or play.created_by_id == user.id or any(s.player_id == user.id for s in play.seats)


def _form_context(db: Session, user: Player, play: Play | None, state: dict, error: str | None = None) -> dict:
    games = db.scalars(select(Game).where(Game.archived.is_(False)).order_by(Game.name)
                       .options(selectinload(Game.categories))).all()
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

    locations = db.scalars(
        select(Play.location).where(Play.location.is_not(None)).group_by(Play.location)
        .order_by(func.count().desc()).limit(20)
    ).all()

    return {
        "play": play,
        "error": error,
        "games": games,
        "locations": locations,
        "boot": {
            "games": [{"id": g.id, "name": g.name, "scoring": g.scoring, "uses_rounds": g.uses_rounds,
                       "round_label": g.round_label or "Round",
                       "categories": [{"id": c.id, "name": c.name, "archived": c.archived} for c in g.categories]}
                      for g in games],
            "players": [{"id": p.id, "name": p.display_name, "initials": p.initials, "color": color_for(p),
                         "avatar": p.avatar_url, "guest": p.source == "local"} for p in players],
            "last_lineup": last_lineup,
            "state": state,
        },
    }


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
    except ValidationError as e:
        msg = "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
        return None, {**raw_state, "game_id": form.get("game_id")}, msg
    except (ValueError, TypeError) as e:
        return None, {**raw_state, "game_id": form.get("game_id")}, str(e) or "Invalid input."

    results = compute(game.scoring, payload)

    play.game_id = game.id
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
               user: Player = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Play).options(selectinload(Play.game), selectinload(Play.seats).selectinload(PlaySeat.player))
    if game:
        q = q.where(Play.game_id == game)
    if player:
        q = q.where(Play.seats.any(PlaySeat.player_id == player))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    page = max(page, 1)
    plays = db.scalars(q.order_by(Play.played_at.desc(), Play.id.desc()).offset((page - 1) * PAGE).limit(PAGE)).all()
    return render(request, "plays/list.html", {
        "plays": plays, "page": page, "pages": max(1, -(-total // PAGE)), "total": total,
        "games": db.scalars(select(Game).order_by(Game.name)).all(),
        "players": db.scalars(select(Player).order_by(Player.display_name)).all(),
        "f_game": game, "f_player": player,
    })


@router.get("/new")
def new_play(request: Request, game: int | None = None, user: Player = Depends(current_user),
             db: Session = Depends(get_db)):
    state = {"game_id": game, "players": [{"player_id": user.id}], "rounds": [],
             "winners_manual": False, "coop_won": None}
    return render(request, "plays/form.html", {**_form_context(db, user, None, state),
                                               "now": datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y-%m-%dT%H:%M")})


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_play(request: Request, background: BackgroundTasks, user: Player = Depends(current_user),
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
    return render(request, "plays/show.html", {"play": play, "seats": seat_order, "can_edit": can_edit(user, play)})


@router.get("/{play_id}/edit")
def edit_play(play_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    play = _get_play(db, play_id)
    if not can_edit(user, play):
        raise HTTPException(403, "You can only edit plays you logged or played in.")
    return render(request, "plays/form.html", {**_form_context(db, user, play, _state_from_play(play)),
                                               "now": play.played_at.strftime("%Y-%m-%dT%H:%M")})


@router.post("/{play_id}", dependencies=[Depends(verify_csrf)])
async def update_play(play_id: int, request: Request, user: Player = Depends(current_user),
                      db: Session = Depends(get_db)):
    play = _get_play(db, play_id)
    if not can_edit(user, play):
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
    if not can_edit(user, play):
        raise HTTPException(403)
    keys = [ph.key for ph in play.photos]
    db.delete(play)
    db.commit()
    for k in keys:
        media.delete_photo(k)
    flash(request, "Play deleted.")
    return RedirectResponse("/plays", status_code=303)
