"""Sync the games list with a BoardGameGeek collection (owned board games).

plan() is a dry run; apply() writes. Matching is by BGG ID, then by name. Existing games only
get *blank* fields filled in, so anything you've edited by hand is left alone.
"""

import json
import logging
import secrets
import time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import bgg, media
from .config import get_settings
from .db import SessionLocal
from .models import AppSetting, Game

log = logging.getLogger(__name__)
FILL = ("year", "min_players", "max_players", "play_time_min")


def get_username(db: Session) -> str:
    row = db.get(AppSetting, "bgg_username")
    return (row.value or "") if row else ""


def set_username(db: Session, name: str) -> None:
    row = db.get(AppSetting, "bgg_username") or AppSetting(key="bgg_username")
    row.value = name
    db.add(row)


def _match(db: Session, item: dict) -> Game | None:
    g = db.scalar(select(Game).where(Game.bgg_id == item["bgg_id"]))
    return g or db.scalar(select(Game).where(func.lower(Game.name) == item["name"].lower(), Game.bgg_id.is_(None)))


def plan(db: Session, items: list[dict]) -> dict:
    add, update, same, matched_ids = [], [], 0, set()
    for it in items:
        g = _match(db, it)
        if g is None:
            add.append(it)
            continue
        matched_ids.add(g.id)
        changes = [f for f in FILL if getattr(g, f) is None and it.get(f) is not None]
        if g.bgg_id is None:
            changes.append("bgg_id")
        if not g.owned:
            changes.append("owned")
        if g.archived:
            changes.append("unarchive")
        if not g.has_cover and it.get("image"):
            changes.append("cover")
        if changes:
            update.append({"game": g, "item": it, "changes": changes})
        else:
            same += 1
    gone = db.scalars(select(Game).where(Game.owned.is_(True), Game.id.not_in(matched_ids or {-1}))
                      .order_by(Game.name)).all()
    # keys avoid dict method names (Jinja's plan.update would resolve to dict.update)
    return {"new_games": add, "updates": update, "same": same, "gone": gone, "total": len(items)}


def apply(db: Session, items: list[dict], *, add_new: bool, unown_gone: bool, archive_gone: bool
          ) -> tuple[dict, list[tuple[int, str]]]:
    """Returns (counts, [(game_id, image_url)] still needing a cover)."""
    counts = {"added": 0, "updated": 0, "unowned": 0, "archived": 0}
    covers: list[tuple[int, str]] = []
    matched_ids: set[int] = set()
    for it in items:
        g = _match(db, it)
        if g is None:
            if not add_new:
                continue
            for candidate in (it["name"], f"{it['name']} ({it['year']})" if it.get("year") else None,
                              f"{it['name']} (BGG {it['bgg_id']})"):
                if candidate and not db.scalar(select(Game).where(func.lower(Game.name) == candidate.lower())):
                    it = {**it, "name": candidate[:200]}  # first free name; another game may own the plain one
                    break
            g = Game(name=it["name"], scoring="high", bgg_id=it["bgg_id"], owned=True,
                     **{f: it.get(f) for f in FILL})
            db.add(g)
            db.flush()
            counts["added"] += 1
        else:
            changed = False
            for f in FILL:
                if getattr(g, f) is None and it.get(f) is not None:
                    setattr(g, f, it[f])
                    changed = True
            if g.bgg_id is None:
                g.bgg_id, changed = it["bgg_id"], True
            if not g.owned or g.archived:
                g.owned, g.archived, changed = True, False, True
            counts["updated"] += int(changed)
        matched_ids.add(g.id)
        if not g.has_cover and it.get("image"):
            covers.append((g.id, it["image"]))

    for g in db.scalars(select(Game).where(Game.owned.is_(True), Game.id.not_in(matched_ids or {-1}))).all():
        if unown_gone or archive_gone:
            g.owned = False
            counts["unowned"] += 1
        if archive_gone:
            g.archived = True
            counts["archived"] += 1
    db.commit()
    return counts, covers


def download_covers(covers: list[tuple[int, str]], pause: float = 1.0) -> None:
    """Background task: fetch BGG cover art one at a time, politely."""
    ok = 0
    for game_id, url in covers:
        try:
            media.save_cover(game_id, bgg.fetch_image(url))
        except (bgg.BggError, media.ImageError) as e:
            log.warning("Cover for game %s failed: %s", game_id, e)
            continue
        with SessionLocal() as db:
            g = db.get(Game, game_id)
            if g:
                g.has_cover = True
                db.commit()
        ok += 1
        time.sleep(pause)
    log.info("Collection covers: %s of %s downloaded", ok, len(covers))


# ---------------------------------------------------------------- staging between preview and apply

def _dir() -> Path:
    d = Path(get_settings().data_dir) / "imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stage(username: str, items: list[dict]) -> str:
    token = secrets.token_hex(16)
    (_dir() / f"collection-{token}.json").write_text(json.dumps({"username": username, "items": items}))
    return token


def load(token: str) -> tuple[str, list[dict]] | None:
    if not token.isalnum() or len(token) != 32:
        return None
    p = _dir() / f"collection-{token}.json"
    if not p.is_file():
        return None
    d = json.loads(p.read_text())
    return d["username"], d["items"]


def discard(token: str) -> None:
    if token.isalnum():
        (_dir() / f"collection-{token}.json").unlink(missing_ok=True)
