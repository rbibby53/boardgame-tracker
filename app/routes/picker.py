import random
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import Game, Play, Player
from ..templating import render

router = APIRouter()
SORTS = {"stale": "Least recently played", "fewest": "Fewest plays", "random": "Random"}


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


@router.get("/pick")
def pick(request: Request, players: str | None = None, minutes: str | None = None, sort: str = "stale",
         surprise: int = 0, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    n, limit = _int(players), _int(minutes)
    sort = sort if sort in SORTS else "stale"
    rows = db.execute(
        select(Game, func.count(Play.id), func.max(Play.played_at), func.avg(Play.duration_min))
        .outerjoin(Play).where(Game.archived.is_(False)).group_by(Game.id)
    ).all()

    results = []
    for g, count, last, avg_dur in rows:
        length = g.play_time_min or (round(avg_dur) if avg_dur else None)
        notes = []
        if n is not None:
            if (g.min_players and n < g.min_players) or (g.max_players and n > g.max_players):
                continue
            if not (g.min_players or g.max_players):
                notes.append("player count not set")
        if limit is not None and length is not None and length > limit:
            continue
        if limit is not None and length is None:
            notes.append("length unknown")
        results.append({"game": g, "plays": count, "last": last, "length": length,
                        "length_src": "box" if g.play_time_min else ("your average" if avg_dur else None),
                        "notes": notes})

    if sort == "stale":
        results.sort(key=lambda r: (r["last"] or datetime.min, r["game"].name.lower()))
    elif sort == "fewest":
        results.sort(key=lambda r: (r["plays"], r["game"].name.lower()))
    else:
        random.shuffle(results)

    chosen = random.choice(results) if surprise and results else None
    return render(request, "pick.html", {
        "results": results, "chosen": chosen, "sorts": SORTS,
        "f": {"players": n, "minutes": limit, "sort": sort}, "searched": players is not None or minutes is not None,
    })
