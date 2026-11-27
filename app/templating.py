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


def pct(n: float | None) -> str:
    return "—" if n is None else f"{n * 100:.0f}%"


templates.env.globals.update(SCORING_TYPES=SCORING_TYPES)
templates.env.filters.update(score=fmt_score, dt=local_dt, utc=utc_to_local, pct=pct)


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    settings = get_settings()
    base = {
        "user": getattr(request.state, "user", None),
        "csrf": csrf_token(request),
        "settings": settings,
        "flash": request.session.pop("flash", None),
    }
    base.update(ctx or {})
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}
