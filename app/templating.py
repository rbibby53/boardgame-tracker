import hashlib
import secrets
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .models import SCORING_TYPES
from .scoring import fmt_score

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
STATIC_DIR = Path(__file__).parent / "static"


def _asset_version() -> str:
    h = hashlib.sha256()
    for f in sorted(STATIC_DIR.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(STATIC_DIR).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version()


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


def local_dt(dt: datetime | None, fmt: str = "%b %-d, %Y %-I:%M %p") -> str:
    """Plays store naive local wall-clock time; audit timestamps are naive UTC."""
    if dt is None:
        return ""
    return dt.strftime(fmt)


def utc_to_local(dt: datetime | None, fmt: str = "%b %-d, %Y %-I:%M %p") -> str:
    if dt is None:
        return "never"
    tz = ZoneInfo(get_settings().timezone)
    return dt.replace(tzinfo=timezone.utc).astimezone(tz).strftime(fmt)


def night_link(dt: datetime | None) -> str:
    from .nights import night_of

    return f"/nights/{night_of(dt).isoformat()}" if dt else "/nights"


def pct(n: float | None) -> str:
    return "—" if n is None else f"{n * 100:.0f}%"


templates.env.globals.update(SCORING_TYPES=SCORING_TYPES, V=ASSET_VERSION)
templates.env.filters.update(score=fmt_score, dt=local_dt, utc=utc_to_local, pct=pct, night=night_link)


def _role_of_factory(request: Request):
    """Lazy: only loads the permissions config if a template asks for someone's role."""
    def role_of(player) -> str:
        from . import permissions
        from .db import SessionLocal

        with SessionLocal() as db:
            return permissions.role_of(player, permissions.load(db))
    return role_of


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    settings = get_settings()
    perms = getattr(request.state, "perms", set())
    base = {
        "user": getattr(request.state, "user", None),
        "role": getattr(request.state, "role", None),
        "can": lambda perm: perm in perms,
        "role_of": _role_of_factory(request),
        "csrf": csrf_token(request),
        "settings": settings,
        "flash": request.session.pop("flash", None),
    }
    base.update(ctx or {})
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}
