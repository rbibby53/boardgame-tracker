"""'What should we play?' votes. Approval voting: tick every game you'd play; most ticks wins.
Ties go to the game played least recently (never-played first), then alphabetical."""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import notify
from ..auth import current_user, verify_csrf
from ..config import get_settings
from ..db import get_db
from ..models import Game, Play, Player, Poll, PollOption, PollVote, utcnow
from ..templating import flash, render

router = APIRouter(prefix="/polls")
MAX_OPTIONS = 20


def _load(db: Session, poll_id: int) -> Poll:
    poll = db.scalar(select(Poll).where(Poll.id == poll_id).options(
        selectinload(Poll.options).selectinload(PollOption.game),
        selectinload(Poll.options).selectinload(PollOption.votes).selectinload(PollVote.player),
    ))
    if poll is None:
        raise HTTPException(404, "Vote not found")
    return poll


def can_manage(user: Player, poll: Poll) -> bool:
    return user.is_admin or poll.created_by_id == user.id


def tally(db: Session, poll: Poll) -> list[dict]:
    game_ids = [o.game_id for o in poll.options]
    last = dict(db.execute(select(Play.game_id, func.max(Play.played_at))
                           .where(Play.game_id.in_(game_ids or [-1])).group_by(Play.game_id)).all())
    rows = [{
        "option": o, "game": o.game, "votes": len(o.votes),
        "voters": sorted((v.player for v in o.votes), key=lambda p: p.display_name),
        "last": last.get(o.game_id),
    } for o in poll.options]
    rows.sort(key=lambda r: (-r["votes"], r["last"] or datetime.min, r["game"].name.lower()))
    return rows


def state(db: Session, poll: Poll, user: Player) -> dict:
    rows = tally(db, poll)
    voters = {v.player_id for o in poll.options for v in o.votes}
    top = rows[0]["votes"] if rows else 0
    return {
        "open": poll.is_open,
        "can_manage": can_manage(user, poll),
        "voters": len(voters),
        "winner": ({"id": poll.winner.id, "name": poll.winner.name} if poll.winner else None),
        "options": [{
            "id": r["option"].id, "game_id": r["game"].id, "name": r["game"].name,
            "cover": r["game"].has_cover, "votes": r["votes"],
            "voters": [p.display_name for p in r["voters"]],
            "mine": any(p.id == user.id for p in r["voters"]),
            "leading": top > 0 and r["votes"] == top,
            "last": r["last"].strftime("%b %-d, %Y") if r["last"] else None,
        } for r in rows],
    }


@router.get("")
def list_polls(request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    polls = db.scalars(select(Poll).options(selectinload(Poll.winner), selectinload(Poll.options))
                       .order_by(Poll.closed_at.is_not(None), Poll.created_at.desc()).limit(30)).all()
    return render(request, "polls/list.html", {"polls": polls})


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_poll(request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    form = await request.form()
    ids = []
    for v in form.getlist("game_ids"):
        try:
            gid = int(v)
        except (TypeError, ValueError):
            continue
        if gid not in ids:
            ids.append(gid)
    games = db.scalars(select(Game).where(Game.id.in_(ids or [-1]), Game.archived.is_(False))).all()
    if len(games) < 2:
        flash(request, "Pick at least two games to vote on.", "err")
        return RedirectResponse(request.headers.get("referer") or "/pick", status_code=303)
    if len(games) > MAX_OPTIONS:
        flash(request, f"Keep it to {MAX_OPTIONS} games or fewer.", "err")
        return RedirectResponse(request.headers.get("referer") or "/pick", status_code=303)
    title = (form.get("title") or "").strip()[:150] or "What should we play?"
    poll = Poll(title=title, created_by_id=user.id)
    by_id = {g.id: g for g in games}
    for gid in ids:
        if gid in by_id:
            poll.options.append(PollOption(game_id=gid))
    db.add(poll)
    db.commit()
    return RedirectResponse(f"/polls/{poll.id}", status_code=303)


@router.get("/{poll_id}")
def show_poll(poll_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    poll = _load(db, poll_id)
    return render(request, "polls/show.html", {"poll": poll, "boot": state(db, poll, user)})


@router.get("/{poll_id}/state")
def poll_state(poll_id: int, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    return state(db, _load(db, poll_id), user)


@router.post("/{poll_id}/vote", dependencies=[Depends(verify_csrf)])
async def vote(poll_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    poll = _load(db, poll_id)
    if not poll.is_open:
        raise HTTPException(409, "This vote is closed.")
    data = await request.json()
    option = next((o for o in poll.options if o.id == data.get("option_id")), None)
    if option is None:
        raise HTTPException(400, "Unknown option.")
    existing = next((v for v in option.votes if v.player_id == user.id), None)
    if data.get("on") and existing is None:
        option.votes.append(PollVote(player_id=user.id))
    elif not data.get("on") and existing is not None:
        option.votes.remove(existing)
    db.commit()
    db.expire_all()
    return state(db, _load(db, poll_id), user)


@router.post("/{poll_id}/close", dependencies=[Depends(verify_csrf)])
def close_poll(poll_id: int, request: Request, background: BackgroundTasks, user: Player = Depends(current_user),
               db: Session = Depends(get_db)):
    poll = _load(db, poll_id)
    if not can_manage(user, poll):
        raise HTTPException(403, "Only whoever started the vote (or an admin) can close it.")
    if poll.is_open:
        rows = tally(db, poll)
        poll.closed_at = utcnow()
        poll.winner_game_id = rows[0]["game"].id if rows and rows[0]["votes"] > 0 else None
        db.commit()
        s = get_settings()
        if s.notify_enabled and poll.winner_game_id:
            name = rows[0]["game"].name
            background.add_task(notify.send_logged, s, {
                "event": "vote_closed", "play_id": None, "game": name, "played_at": None, "location": None,
                "headline": f"🗳️ {name} wins the vote", "body": f"{poll.title}: {rows[0]['votes']} votes",
                "url": f"{s.base_url}/polls/{poll.id}", "players": [], "winners": [], "coop_won": None,
            })
    return RedirectResponse(f"/polls/{poll.id}", status_code=303)


@router.post("/{poll_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_poll(poll_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    poll = _load(db, poll_id)
    if not can_manage(user, poll):
        raise HTTPException(403)
    db.delete(poll)
    db.commit()
    flash(request, "Vote deleted.")
    return RedirectResponse("/polls", status_code=303)
