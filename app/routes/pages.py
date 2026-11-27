from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import stats
from ..auth import current_user
from ..db import get_db
from ..config import get_settings
from ..models import Game, Player
from ..templating import render

router = APIRouter()


@router.get("/")
def dashboard(request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    all_plays = stats.load_plays(db)
    local_now = datetime.now(ZoneInfo(get_settings().timezone)).replace(tzinfo=None)
    recent_cut = local_now - timedelta(days=90)
    recent = [p for p in all_plays if p.played_at >= recent_cut]
    my = [p for p in all_plays if any(s.player_id == user.id for s in p.seats)]
    my_wins = sum(1 for p in my for s in p.seats if s.player_id == user.id and s.is_winner)
    return render(request, "dashboard.html", {
        "latest": list(reversed(all_plays[-8:])),
        "board_all": stats.leaderboard(all_plays)[:10],
        "board_recent": stats.leaderboard(recent)[:10],
        "most_played": stats.most_played(all_plays),
        "totals": {
            "plays": len(all_plays),
            "games": db.scalar(select(func.count()).select_from(Game).where(Game.archived.is_(False))),
            "players": db.scalar(select(func.count()).select_from(Player).where(Player.is_active.is_(True))),
            "my_plays": len(my),
            "my_wins": my_wins,
        },
        "wins_series": stats.cumulative_wins_series(all_plays),
    })


@router.get("/healthz", include_in_schema=False)
def healthz(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"ok": True}

