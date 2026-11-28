from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import nights as N
from .. import stats
from ..auth import current_user, require, verify_csrf
from ..db import get_db
from ..models import GameNight, Play, Player
from ..templating import flash, render

router = APIRouter(prefix="/nights")
PAGE = 30


def _parse(day: str) -> date:
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise HTTPException(404, "Not a valid date") from None


@router.get("")
def list_nights(request: Request, page: int = 1, user: Player = Depends(current_user),
                db: Session = Depends(get_db)):
    all_nights = N.group(stats.load_plays(db))
    page = max(page, 1)
    chunk = all_nights[(page - 1) * PAGE: page * PAGE]
    meta = {n.night_date: n for n in db.scalars(
        select(GameNight).where(GameNight.night_date.in_([n.day for n in chunk] or [date.min]))).all()}
    return render(request, "nights/list.html", {
        "nights": chunk, "meta": meta, "page": page,
        "pages": max(1, -(-len(all_nights) // PAGE)), "total": len(all_nights),
    })


@router.get("/{day}")
def show_night(day: str, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    d = _parse(day)
    start, end = N.bounds(d)
    in_window = [p for p in stats.load_plays(db, since=start) if p.played_at < end]
    plays = [p for p in in_window if not p.live_night_id]  # live nights have their own pages
    live_nights = list({p.live_night_id: p.live_night for p in in_window if p.live_night_id}.values())
    if not plays:
        if len(live_nights) == 1:
            return RedirectResponse(f"/live/{live_nights[0].id}", status_code=303)
        if not live_nights:
            raise HTTPException(404, "No plays logged that night")
    night = N.Night(d, plays)
    all_nights = N.group(stats.load_plays(db))
    days = sorted({n.day for n in all_nights}, reverse=True)
    i = days.index(d)
    photos = [(p, ph) for p in plays for ph in p.photos]
    return render(request, "nights/show.html", {
        "night": night,
        "meta": db.scalar(select(GameNight).where(GameNight.night_date == d)),
        "newer": days[i - 1] if i > 0 else None,
        "older": days[i + 1] if i + 1 < len(days) else None,
        "photos": photos,
        "live_nights": live_nights,
    })


@router.post("/{day}", dependencies=[Depends(verify_csrf)])
async def save_night(day: str, request: Request, user: Player = Depends(require("edit_nights")),
                     db: Session = Depends(get_db)):
    d = _parse(day)
    start, end = N.bounds(d)
    if not db.scalar(select(Play.id).where(Play.played_at >= start, Play.played_at < end).limit(1)):
        raise HTTPException(404)
    form = await request.form()
    meta = db.scalar(select(GameNight).where(GameNight.night_date == d)) or GameNight(night_date=d)
    meta.title = (form.get("title") or "").strip()[:150] or None
    meta.notes = (form.get("notes") or "").strip() or None
    db.add(meta)
    db.commit()
    flash(request, "Saved.")
    return RedirectResponse(f"/nights/{d.isoformat()}", status_code=303)
