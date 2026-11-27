from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import ratings, stats
from ..auth import current_user
from ..db import get_db
from ..config import get_settings
from ..models import Game, Player
from ..templating import ASSET_VERSION, STATIC_DIR, render

router = APIRouter()


@router.get("/")
def dashboard(request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    all_plays = stats.load_plays(db)
    local_now = datetime.now(ZoneInfo(get_settings().timezone)).replace(tzinfo=None)
    recent_cut = local_now - timedelta(days=90)
    recent = [p for p in all_plays if p.played_at >= recent_cut]
    my = [p for p in all_plays if any(s.player_id == user.id for s in p.seats)]
    my_wins = sum(1 for p in my for s in p.seats if s.player_id == user.id and s.is_winner)
    rt = ratings.table(db, all_plays)
    rated = [p for p in db.scalars(select(Player)).all() if p.id in rt.overall and p.is_active]
    board_rating = sorted(rated, key=lambda p: -rt.overall_for(p.id))[:10]
    return render(request, "dashboard.html", {
        "rt": rt,
        "board_rating": board_rating,
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



_SW = (STATIC_DIR / "sw.js").read_text().replace("__ASSET_VERSION__", ASSET_VERSION)


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return Response(_SW, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    s = get_settings()
    return JSONResponse({
        "name": s.app_name,
        "short_name": s.app_name[:12],
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f6f5f1",
        "theme_color": "#2f6fdb",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [{"name": "Log a play", "url": "/plays/new",
                       "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]}],
    }, media_type="application/manifest+json")


@router.get("/offline", include_in_schema=False)
def offline(request: Request):
    return render(request, "offline.html")
