import asyncio
import csv
import io
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from .. import notify, ratings, stats
from ..authentik import SyncError, run_sync
from ..auth import current_user, require_admin, verify_csrf
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import Play, PlaySeat, Player, Round, RoundScore, SyncLog
from ..scoring import fmt_score
from ..templating import flash, render

router = APIRouter()


@router.get("/admin")
def admin_home(request: Request, user: Player = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    return render(request, "admin/index.html", {
        "logs": db.scalars(select(SyncLog).order_by(SyncLog.id.desc()).limit(15)).all(),
        "guests": db.scalars(select(Player).where(Player.source == "local").order_by(Player.display_name)).all(),
        "sso_players": db.scalars(select(Player).where(Player.source == "sso", Player.is_active.is_(True))
                                  .order_by(Player.display_name)).all(),
        "cfg": {
            "sync_enabled": s.sync_enabled,
            "authentik_url": s.authentik_url,
            "group": s.authentik_group or "(all internal/external users)",
            "sub_mode": s.authentik_sub_mode,
            "interval": s.authentik_sync_interval_minutes,
            "admin_group": s.oidc_admin_group or "(not set: Authentik superusers become admins via sync)",
            "allowed_group": s.oidc_allowed_group or "(not set)",
            "issuer": s.oidc_issuer,
            "notify": [n for n, on in (("ntfy", s.ntfy_url), ("Discord", s.discord_webhook_url),
                                       ("webhook", s.notify_webhook_url)) if on],
        },
    })


def _sync_in_thread():
    with SessionLocal() as db:
        return run_sync(db, get_settings())


@router.post("/admin/sync", dependencies=[Depends(verify_csrf)])
async def sync_now(request: Request, user: Player = Depends(require_admin)):
    if not get_settings().sync_enabled:
        flash(request, "Sync is not configured (AUTHENTIK_URL / AUTHENTIK_TOKEN).", "err")
        return RedirectResponse("/admin", status_code=303)
    try:
        res = await asyncio.to_thread(_sync_in_thread)
        flash(request, f"Synced {res.total} users: {res.created} new, {res.updated} updated, "
                       f"{res.deactivated} deactivated.")
    except SyncError as e:
        flash(request, str(e), "err")
    except Exception as e:  # noqa: BLE001
        flash(request, f"Sync failed: {e}", "err")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/notify-test", dependencies=[Depends(verify_csrf)])
async def notify_test(request: Request, user: Player = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    if not s.notify_enabled:
        flash(request, "No notification targets configured (NTFY_URL / DISCORD_WEBHOOK_URL / NOTIFY_WEBHOOK_URL).", "err")
        return RedirectResponse("/admin", status_code=303)
    msg = {"event": "test", "play_id": None, "game": "Test", "played_at": None, "location": None,
           "headline": f"🎲 Test from {s.app_name}", "body": f"Sent by {user.display_name}. Notifications work!",
           "url": s.base_url + "/", "players": [], "winners": [], "coop_won": None}
    errors = await asyncio.to_thread(notify.send, s, msg)
    flash(request, "Test sent." if not errors else "Some targets failed: " + "; ".join(errors), "err" if errors else "ok")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/players/{player_id}/active", dependencies=[Depends(verify_csrf)])
def toggle_active(player_id: int, request: Request, user: Player = Depends(require_admin),
                  db: Session = Depends(get_db)):
    p = db.get(Player, player_id)
    if p is None or p.source != "local":
        raise HTTPException(400, "Only guest players can be toggled here; SSO players follow Authentik.")
    p.is_active = not p.is_active
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/merge", dependencies=[Depends(verify_csrf)])
def merge_guest(request: Request, guest_id: int = Form(...), target_id: int = Form(...),
                user: Player = Depends(require_admin), db: Session = Depends(get_db)):
    """Fold a guest's history into another player (e.g. a guest who later got an SSO account)."""
    guest, target = db.get(Player, guest_id), db.get(Player, target_id)
    if guest is None or target is None or guest.id == target.id or guest.source != "local":
        raise HTTPException(400, "Pick a guest and a different target player.")
    clash = db.scalars(
        select(PlaySeat.play_id).where(PlaySeat.player_id == guest.id)
        .intersect(select(PlaySeat.play_id).where(PlaySeat.player_id == target.id))
    ).all()
    if clash:
        flash(request, f"Both players are in the same play ({', '.join(f'#{c}' for c in clash)}); "
                       "fix those plays first.", "err")
        return RedirectResponse("/admin", status_code=303)
    db.execute(update(PlaySeat).where(PlaySeat.player_id == guest.id).values(player_id=target.id))
    db.execute(update(RoundScore).where(RoundScore.player_id == guest.id).values(player_id=target.id))
    db.execute(update(Play).where(Play.created_by_id == guest.id).values(created_by_id=target.id))
    db.delete(guest)
    db.commit()
    ratings.invalidate()
    flash(request, f"Merged {guest.display_name} into {target.display_name}.")
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------- export / API


@router.get("/export/plays.csv")
def export_csv(user: Player = Depends(current_user), db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["play_id", "played_at", "game", "location", "duration_min", "expansions", "player",
                "score", "place", "winner", "team", "notes"])
    for play in stats.load_plays(db):
        for s in play.seats:
            w.writerow([play.id, play.played_at.isoformat(), play.game.name, play.location or "",
                        play.duration_min or "", play.expansions or "", s.player.display_name,
                        fmt_score(s.score) if s.score is not None else "", s.place or "",
                        int(s.is_winner), s.team or "", play.notes or ""])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="plays.csv"'})


@router.get("/export/plays.json")
def export_json(user: Player = Depends(current_user), db: Session = Depends(get_db)):
    plays = db.scalars(select(Play).options(selectinload(Play.rounds).selectinload(Round.scores))
                       .order_by(Play.played_at)).all()
    full = {p.id: p for p in stats.load_plays(db)}
    out = []
    for p in plays:
        fp = full[p.id]
        out.append({
            "id": p.id, "game": fp.game.name, "scoring": fp.game.scoring,
            "played_at": p.played_at.isoformat(), "location": p.location, "duration_min": p.duration_min,
            "expansions": p.expansions, "notes": p.notes, "coop_won": p.coop_won,
            "players": [{"player": s.player.display_name, "username": s.player.username, "score": s.score,
                         "place": s.place, "winner": s.is_winner, "team": s.team} for s in fp.seats],
            "rounds": [{"number": r.number, "label": r.label,
                        "scores": [{"player_id": rs.player_id, "score": rs.score, "won": rs.won}
                                   for rs in r.scores]} for r in p.rounds],
        })
    return Response(json.dumps({"plays": out}, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="plays.json"'})
