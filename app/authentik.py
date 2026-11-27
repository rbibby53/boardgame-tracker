"""Pull the player roster from the Authentik API (GET /api/v3/core/users/)."""

import json
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import SUB_MODE_FIELDS, Settings
from .models import Player, SyncLog, utcnow

log = logging.getLogger(__name__)


class SyncError(Exception):
    pass


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    total: int = 0


def _field(user: dict, path: str):
    val = user
    for part in path.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return None if val is None else str(val)


def sub_for(user: dict, sub_mode: str) -> str | None:
    return _field(user, SUB_MODE_FIELDS[sub_mode])


def avatar_for(user: dict, base_url: str) -> str | None:
    av = user.get("avatar") or ""
    if av.startswith(("http://", "https://")):
        return av
    if av.startswith("/") and "user_default" not in av:
        return base_url + av
    return None  # data: URIs and the default silhouette -> we render initials


def group_names(user: dict) -> set[str]:
    return {g.get("name") for g in user.get("groups_obj") or [] if g.get("name")}


def fetch_users(settings: Settings, client: httpx.Client | None = None) -> list[dict]:
    params: list[tuple[str, str]] = [
        ("is_active", "true"),
        ("type", "internal"),
        ("type", "external"),
        ("include_groups", "true"),
        ("page_size", "100"),
        ("ordering", "username"),
    ]
    if settings.authentik_group:
        params.append(("groups_by_name", settings.authentik_group))

    own = client is None
    client = client or httpx.Client(
        base_url=settings.authentik_url,
        headers={"Authorization": f"Bearer {settings.authentik_token}", "Accept": "application/json"},
        timeout=20,
        verify=settings.authentik_verify_tls,
    )
    users: list[dict] = []
    try:
        page = 1
        while page:
            resp = client.get("/api/v3/core/users/", params=params + [("page", str(page))])
            if resp.status_code in (401, 403):
                raise SyncError(
                    f"Authentik API returned {resp.status_code}. Check AUTHENTIK_TOKEN and that the "
                    "token's user can view users and groups."
                )
            resp.raise_for_status()
            data = resp.json()
            users.extend(data.get("results", []))
            nxt = (data.get("pagination") or {}).get("next") or 0
            page = int(nxt) if nxt and int(nxt) > page else 0
    except httpx.HTTPError as e:
        raise SyncError(f"Authentik API request failed: {e}") from e
    finally:
        if own:
            client.close()
    return users


def apply_users(db: Session, settings: Settings, users: list[dict]) -> SyncResult:
    res = SyncResult(total=len(users))
    now = utcnow()
    seen: set[int] = set()

    for u in users:
        sub = sub_for(u, settings.authentik_sub_mode)
        username = u.get("username")
        if not sub:
            log.warning("Skipping Authentik user %s: no value for sub mode %s", username, settings.authentik_sub_mode)
            continue

        player = db.scalar(select(Player).where(Player.sub == sub))
        if player is None and username:
            player = db.scalar(
                select(Player).where(Player.source == "sso", Player.username == username, Player.sub.is_(None))
            )
        created = player is None
        if created:
            player = Player(source="sso", sub=sub, display_name=username or "Unknown")
            db.add(player)

        player.sub = sub
        player.username = username
        player.display_name = (u.get("name") or "").strip() or username or player.display_name
        player.email = u.get("email") or None
        player.avatar_url = avatar_for(u, settings.authentik_url)
        player.is_active = True
        player.synced_at = now
        player.groups = json.dumps(sorted(group_names(u)))
        if settings.oidc_admin_group:
            player.is_admin = settings.oidc_admin_group in group_names(u)
        elif u.get("is_superuser"):
            player.is_admin = True

        db.flush()
        seen.add(player.id)
        if created:
            res.created += 1
        else:
            res.updated += 1

    stale = db.scalars(
        select(Player).where(Player.source == "sso", Player.is_active.is_(True), Player.id.not_in(seen or {-1}))
    ).all()
    for p in stale:
        p.is_active = False
        res.deactivated += 1

    return res


def run_sync(db: Session, settings: Settings, client: httpx.Client | None = None) -> SyncResult:
    try:
        users = fetch_users(settings, client)
        if not users:
            # Guard against wiping the roster on a misconfigured group filter.
            raise SyncError(
                "Authentik returned zero users"
                + (f" in group '{settings.authentik_group}'" if settings.authentik_group else "")
                + "; nothing changed."
            )
        res = apply_users(db, settings, users)
        db.add(SyncLog(ok=True, created=res.created, updated=res.updated, deactivated=res.deactivated,
                       message=f"{res.total} users from Authentik"))
        db.commit()
        log.info("Authentik sync: %s", res)
        return res
    except Exception as e:
        db.rollback()
        db.add(SyncLog(ok=False, message=str(e)[:2000]))
        db.commit()
        log.error("Authentik sync failed: %s", e)
        raise
