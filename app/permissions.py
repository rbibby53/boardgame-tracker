"""Roles and permissions.

A signed-in user's role is, in order:
  1. admin, if they're in OIDC_ADMIN_GROUP (or an Authentik superuser when that isn't set): can't be locked out
  2. a per-user override set on Admin -> Permissions
  3. the highest role among their Authentik groups' mappings
  4. the default role
Each role (except admin, which has everything) has an editable set of permissions.
"""

import json
import threading
import time

from sqlalchemy.orm import Session

from .models import AppSetting, Player

ROLES = ["viewer", "player", "manager", "admin"]
ROLE_LABELS = {"viewer": "Viewer", "player": "Player", "manager": "Manager", "admin": "Admin"}

PERMS = {
    "log_plays": "Log plays; edit plays they logged or played in",
    "edit_all_plays": "Edit or delete any play",
    "upload_photos": "Add photos to plays they can edit",
    "manage_games": "Add and edit games (details, score sheets, covers, BGG lookup)",
    "archive_games": "Archive and delete games",
    "manage_guests": "Add and rename guest players",
    "vote": "Vote on what to play",
    "start_votes": "Start votes (and close their own)",
    "edit_nights": "Edit game night titles and notes",
    "export": "Download CSV / JSON exports",
}

DEFAULT_MATRIX = {
    "viewer": ["vote"],
    "player": ["log_plays", "upload_photos", "manage_games", "manage_guests", "vote", "start_votes",
               "edit_nights", "export"],
    "manager": [p for p in PERMS],
}

KEY = "permissions"
_lock = threading.Lock()
_cache: tuple[float, dict] | None = None
TTL = 30.0


def _defaults() -> dict:
    return {"matrix": {r: list(v) for r, v in DEFAULT_MATRIX.items()}, "group_roles": {}, "default_role": "player"}


def _clean(cfg: dict) -> dict:
    out = _defaults()
    m = cfg.get("matrix") or {}
    for role in ("viewer", "player", "manager"):
        if role in m:
            out["matrix"][role] = [p for p in m[role] if p in PERMS]
    out["group_roles"] = {str(g)[:150]: r for g, r in (cfg.get("group_roles") or {}).items()
                          if r in ROLES and str(g).strip()}
    if cfg.get("default_role") in ROLES and cfg.get("default_role") != "admin":
        out["default_role"] = cfg["default_role"]
    return out


def load(db: Session) -> dict:
    global _cache
    with _lock:
        if _cache and time.monotonic() - _cache[0] < TTL:
            return _cache[1]
    row = db.get(AppSetting, KEY)
    cfg = _clean(json.loads(row.value)) if row and row.value else _defaults()
    with _lock:
        _cache = (time.monotonic(), cfg)
    return cfg


def save(db: Session, cfg: dict) -> dict:
    global _cache
    cfg = _clean(cfg)
    row = db.get(AppSetting, KEY) or AppSetting(key=KEY)
    row.value = json.dumps(cfg)
    db.add(row)
    db.commit()
    with _lock:
        _cache = None
    return cfg


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def groups_of(p: Player) -> list[str]:
    try:
        g = json.loads(p.groups or "[]")
        return [str(x) for x in g] if isinstance(g, list) else []
    except ValueError:
        return []


def role_source(p: Player, cfg: dict) -> tuple[str, str]:
    """(role, why) for display on the permissions page."""
    if p.is_admin:
        return "admin", "admin group"
    if p.role_override in ROLES:
        return p.role_override, "set here"
    mapped = [cfg["group_roles"][g] for g in groups_of(p) if g in cfg["group_roles"]]
    if mapped:
        best = max(mapped, key=ROLES.index)
        return best, "Authentik group"
    return cfg["default_role"], "default"


def role_of(p: Player, cfg: dict) -> str:
    return role_source(p, cfg)[0]


def perms_for_role(role: str, cfg: dict) -> set[str]:
    if role == "admin":
        return set(PERMS) | {"admin"}
    return set(cfg["matrix"].get(role, []))


def perms_of(p: Player, cfg: dict) -> set[str]:
    return perms_for_role(role_of(p, cfg), cfg)
