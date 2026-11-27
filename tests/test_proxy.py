import pytest
from pydantic import ValidationError
from starlette.responses import RedirectResponse

from app.config import Settings, get_settings


def test_base_url_rejects_paths():
    with pytest.raises(ValidationError, match="can't include a path"):
        Settings(secret_key="k" * 40, dev_mode=True, base_url="https://example.com/games")
    s = Settings(secret_key="k" * 40, dev_mode=True, base_url="https://Games.Example.com/")
    assert s.base_url == "https://Games.Example.com" and s.base_host == "games.example.com"


def test_login_moves_to_canonical_host_once(client, monkeypatch):
    from app import auth

    s = get_settings()
    monkeypatch.setattr(s, "dev_mode", False)
    monkeypatch.setattr(s, "base_url", "https://games.example.com")
    started = []

    class Fake:
        async def authorize_redirect(self, request, redirect_uri):
            started.append(redirect_uri)
            return RedirectResponse("https://auth.example.com/authorize", status_code=302)

    monkeypatch.setattr(auth, "_client", lambda: Fake())

    # Opened by IP: bounce to BASE_URL before starting OIDC
    r = client.get("/login?next=/plays", headers={"host": "192.168.1.50:8000"}, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "https://games.example.com/login?canon=1&next=/plays"
    assert not started

    # Through the proxy (correct Host, X-Forwarded-Proto https): straight to Authentik
    r = client.get("/login", headers={"host": "games.example.com", "x-forwarded-proto": "https"},
                   follow_redirects=False)
    assert r.status_code == 302 and started == ["https://games.example.com/auth/callback"]

    # Proxy that rewrites Host but sends X-Forwarded-Host is recognized
    r = client.get("/login", headers={"host": "boardgames:8000", "x-forwarded-host": "games.example.com"},
                   follow_redirects=False)
    assert r.status_code == 302

    # Loop guard: a proxy that hides the real host entirely still gets through on the second hop
    r = client.get("/login?canon=1", headers={"host": "boardgames:8000"}, follow_redirects=False)
    assert r.status_code == 302 and len(started) == 3
