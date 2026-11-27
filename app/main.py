import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .authentik import run_sync
from .config import get_settings
from .db import SessionLocal
from .routes import admin, games, media, nights, pages, picker, players, plays
from .templating import render

log = logging.getLogger("boardgames")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")


async def _sync_loop():
    s = get_settings()
    interval = max(5, s.authentik_sync_interval_minutes) * 60
    await asyncio.sleep(5)
    while True:
        try:
            def _go():
                with SessionLocal() as db:
                    run_sync(db, s)

            await asyncio.to_thread(_go)
        except Exception:  # logged + recorded in sync_log by run_sync
            pass
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    run_migrations()
    if s.dev_mode:
        log.warning("DEV_MODE is on: SSO is bypassed. Never expose this instance.")
    task = None
    if s.sync_enabled and s.authentik_sync_interval_minutes > 0:
        task = asyncio.create_task(_sync_loop())
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=s.secret_key,
        session_cookie="bg_session",
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=s.secure_cookies,
    )
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    for r in (auth.router, pages.router, plays.router, games.router, players.router, nights.router, picker.router, media.router, admin.router):
        app.include_router(r)

    @app.exception_handler(auth.LoginRequired)
    async def _login_required(request: Request, exc):
        if request.url.path.startswith(("/api/", "/export/")) or request.method != "GET":
            return JSONResponse({"error": "login required"}, status_code=401)
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={nxt}", status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if request.headers.get("accept", "").startswith("application/json") or \
                request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        titles = {403: "Not allowed", 404: "Not found", 400: "Bad request"}
        return render(request, "error.html", {"title": titles.get(exc.status_code, "Error"),
                                              "detail": exc.detail}, exc.status_code)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    return app


app = create_app()
