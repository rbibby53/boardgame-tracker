import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import fun, ratings, stats
from ..auth import current_user, verify_csrf
from ..db import get_db
from ..models import Player, PlaySeat
from ..templating import flash, render

router = APIRouter(prefix="/players")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _can_edit(user: Player, p: Player) -> bool:
    return user.is_admin or user.id == p.id or (p.source == "local" and user.is_active)


@router.get("")
def list_players(request: Request, inactive: bool = False, user: Player = Depends(current_user),
                 db: Session = Depends(get_db)):
    counts = dict(db.execute(select(PlaySeat.player_id, func.count()).group_by(PlaySeat.player_id)).all())
    wins = dict(db.execute(select(PlaySeat.player_id, func.count()).where(PlaySeat.is_winner.is_(True))
                           .group_by(PlaySeat.player_id)).all())
    players = db.scalars(select(Player).where(Player.is_active.is_(not inactive)).order_by(Player.display_name)).all()
    return render(request, "players/list.html", {
        "players": players, "counts": counts, "wins": wins, "inactive": inactive,
        "color_for": stats.color_for,
    })


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_guest(request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    """Add a local (non-SSO) player. Accepts a form post or JSON (from the log-a-play page)."""
    is_json = request.headers.get("content-type", "").startswith("application/json")
    data = await request.json() if is_json else dict(await request.form())
    name = str(data.get("name") or "").strip()[:150]
    if not name:
        if is_json:
            return JSONResponse({"error": "Name is required"}, 422)
        flash(request, "Name is required.", "err")
        return RedirectResponse("/players", status_code=303)
    existing = db.scalar(select(Player).where(func.lower(Player.display_name) == name.lower(),
                                              Player.is_active.is_(True)))
    p = existing or Player(display_name=name, source="local")
    if not existing:
        db.add(p)
        db.commit()
    if is_json:
        return {"id": p.id, "name": p.display_name, "initials": p.initials, "color": stats.color_for(p),
                "avatar": p.avatar_url, "guest": p.source == "local", "existing": bool(existing)}
    flash(request, f"{'Found' if existing else 'Added'} {p.display_name}.")
    return RedirectResponse(f"/players/{p.id}", status_code=303)


@router.get("/{player_id}")
def show_player(player_id: int, request: Request, user: Player = Depends(current_user),
                db: Session = Depends(get_db)):
    p = db.get(Player, player_id)
    if p is None:
        raise HTTPException(404)
    plays = stats.load_plays(db, player_id=player_id)
    seats = [s for pl in plays for s in pl.seats if s.player_id == player_id]
    comp = [s for s in seats if s.play.game.scoring != "coop"]
    coop = [s for s in seats if s.play.game.scoring == "coop"]
    rt = ratings.table(db)
    st = fun.streaks(plays).get(player_id, {"current": 0, "best": 0})
    return render(request, "players/show.html", {
        "streak": st,
        "rivals": fun.rivals(player_id, plays),
        "achievements": fun.achievements(p, plays),
        "p": p,
        "rt": rt,
        "rating": rt.overall_for(p.id),
        "rating_series": ratings.series(rt, plays, only_player=player_id),
        "color": stats.color_for(p),
        "can_edit": _can_edit(user, p),
        "summary": {
            "plays": len(plays),
            "wins": sum(s.is_winner for s in comp),
            "comp": len(comp),
            "coop": len(coop),
            "coop_wins": sum(s.is_winner for s in coop),
            "games": len({pl.game_id for pl in plays}),
        },
        "per_game": stats.per_game_for_player(player_id, plays),
        "h2h": stats.head_to_head(player_id, plays),
        "recent": list(reversed(plays[-10:])),
        "wins_series": stats.cumulative_wins_series(plays, only_player=player_id),
    })


@router.post("/{player_id}", dependencies=[Depends(verify_csrf)])
async def update_player(player_id: int, request: Request, user: Player = Depends(current_user),
                        db: Session = Depends(get_db)):
    p = db.get(Player, player_id)
    if p is None:
        raise HTTPException(404)
    if not _can_edit(user, p):
        raise HTTPException(403)
    form = await request.form()
    color = (form.get("color") or "").strip()
    p.color = color if HEX.match(color) else None
    if p.source == "local":
        name = (form.get("display_name") or "").strip()[:150]
        if name:
            p.display_name = name
    db.commit()
    flash(request, "Saved.")
    return RedirectResponse(f"/players/{p.id}", status_code=303)
