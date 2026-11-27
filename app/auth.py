import json
import logging
import secrets
from urllib.parse import quote, urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import permissions
from .config import get_settings
from .db import get_db
from .models import Player, utcnow
from .templating import render

log = logging.getLogger(__name__)
router = APIRouter()
oauth = OAuth()
_registered = False


class LoginRequired(Exception):
    pass


def _client():
    global _registered
    s = get_settings()
    if not _registered:
        oauth.register(
            name="authentik",
            client_id=s.oidc_client_id,
            client_secret=s.oidc_client_secret,
            server_metadata_url=s.oidc_metadata_url,
            client_kwargs={"scope": s.oidc_scopes, "code_challenge_method": "S256"},
        )
        _registered = True
    return oauth.authentik


# ---------------------------------------------------------------- dependencies


def current_user(request: Request, db: Session = Depends(get_db)) -> Player:
    pid = request.session.get("player_id")
    player = db.get(Player, pid) if pid else None
    if player is None or not player.is_active:
        request.session.pop("player_id", None)
        raise LoginRequired()
    cfg = permissions.load(db)
    request.state.user = player
    request.state.role = permissions.role_of(player, cfg)
    request.state.perms = permissions.perms_for_role(request.state.role, cfg)
    return player


def can(request: Request, perm: str) -> bool:
    return perm in getattr(request.state, "perms", set())


def require(perm: str):
    """Dependency: signed in AND holding `perm`."""
    def dep(request: Request, user: Player = Depends(current_user)) -> Player:
        if perm not in request.state.perms:
            label = permissions.PERMS.get(perm, "Admin")
            raise HTTPException(403, f"Your role doesn't allow this ({label.split(';')[0].lower()}). "
                                     "Ask an admin to change it.")
        return user
    return dep


require_admin = require("admin")


async def verify_csrf(request: Request) -> None:
    expected = request.session.get("csrf")
    sent = request.headers.get("x-csrf-token")
    if sent is None:
        form = await request.form()
        sent = form.get("csrf")
    if not expected or not sent or not secrets.compare_digest(str(sent), expected):
        raise HTTPException(400, "Form expired or invalid (CSRF). Go back, refresh, and try again.")


# ---------------------------------------------------------------- user linking


def upsert_from_claims(db: Session, claims: dict) -> Player:
    s = get_settings()
    sub = claims.get("sub")
    username = claims.get("preferred_username") or claims.get("nickname")
    email = claims.get("email")
    if not sub:
        raise HTTPException(400, "Identity provider did not return a subject (sub).")

    player = db.scalar(select(Player).where(Player.sub == sub))
    if player is None and username:
        player = db.scalar(select(Player).where(Player.source == "sso", Player.username == username))
    if player is None and email:
        player = db.scalar(select(Player).where(Player.source == "sso", func.lower(Player.email) == email.lower()))
    if player is None:
        player = Player(source="sso", display_name=username or email or "Player")
        db.add(player)

    player.sub = sub
    player.username = username or player.username
    player.email = email or player.email
    player.display_name = (claims.get("name") or "").strip() or username or player.display_name
    player.is_active = True
    player.last_login = utcnow()
    groups = claims.get("groups")
    if isinstance(groups, list):
        player.groups = json.dumps(sorted({str(g) for g in groups}))
        if s.oidc_admin_group:
            player.is_admin = s.oidc_admin_group in groups
    db.commit()
    return player


# ---------------------------------------------------------------- routes


def _safe_next(nxt: str | None) -> str:
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return "/"


@router.get("/login")
async def login(request: Request, next: str | None = None, canon: int = 0, db: Session = Depends(get_db)):
    s = get_settings()
    # Sign-in must start on the BASE_URL host, or the state cookie is set on the wrong host and the
    # callback fails ("mismatching state"). Redirect once; `canon` stops a loop if a proxy rewrites Host.
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if not s.dev_mode and not canon and host and host.lower() != s.base_host:
        return RedirectResponse(f"{s.base_url}/login?canon=1&next={quote(_safe_next(next))}", status_code=307)
    request.session["next"] = _safe_next(next)
    if s.dev_mode:
        players = db.scalars(select(Player).where(Player.is_active.is_(True)).order_by(Player.display_name)).all()
        return render(request, "dev_login.html", {"players": players})
    try:
        return await _client().authorize_redirect(request, s.base_url + "/auth/callback")
    except Exception as e:  # network / discovery problems
        log.exception("OIDC discovery failed")
        return render(request, "error.html", {"title": "Can't reach SSO", "detail": str(e)}, 502)


@router.get("/auth/callback")
async def callback(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    client = _client()
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as e:
        return render(request, "error.html", {"title": "Sign-in failed", "detail": e.description or e.error}, 400)

    claims = dict(token.get("userinfo") or {})
    try:
        claims.update({k: v for k, v in (await client.userinfo(token=token)).items() if v is not None})
    except Exception:
        log.debug("userinfo endpoint call failed; using id_token claims only", exc_info=True)

    if s.oidc_allowed_group and s.oidc_allowed_group not in (claims.get("groups") or []):
        return render(request, "error.html", {
            "title": "Not allowed",
            "detail": f"Your account isn't in the '{s.oidc_allowed_group}' group.",
        }, 403)

    player = upsert_from_claims(db, claims)
    nxt = request.session.pop("next", "/")
    request.session.clear()
    request.session["player_id"] = player.id
    return RedirectResponse(_safe_next(nxt), status_code=303)


@router.post("/dev-login")
async def dev_login(
    request: Request,
    player_id: int | None = Form(None),
    new_name: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not get_settings().dev_mode:
        raise HTTPException(404)
    if new_name and new_name.strip():
        player = Player(display_name=new_name.strip()[:150], source="local", is_admin=True)
        db.add(player)
        db.commit()
    else:
        player = db.get(Player, player_id) if player_id else None
        if player is None:
            raise HTTPException(400, "Pick a player")
    nxt = request.session.pop("next", "/")
    request.session.clear()
    request.session["player_id"] = player.id
    return RedirectResponse(_safe_next(nxt), status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(request: Request):
    request.session.clear()
    s = get_settings()
    if s.dev_mode:
        return RedirectResponse("/login", status_code=303)
    try:
        meta = await _client().load_server_metadata()
        end = meta.get("end_session_endpoint")
    except Exception:
        end = None
    if end:
        return RedirectResponse(
            end + "?" + urlencode({"client_id": s.oidc_client_id, "post_logout_redirect_uri": s.base_url + "/"}),
            status_code=303,
        )
    return RedirectResponse("/", status_code=303)
