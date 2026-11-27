import json

import httpx
import respx
from sqlalchemy import select

from app.config import get_settings
from app.models import Game, Player

from .conftest import login


@respx.mock
def test_new_play_notifies_all_targets(client, db, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ntfy_url", "https://ntfy.example.test/gamenight")
    monkeypatch.setattr(s, "ntfy_token", "tk")
    monkeypatch.setattr(s, "discord_webhook_url", "https://discord.example.test/api/webhooks/1/x")
    monkeypatch.setattr(s, "notify_webhook_url", "https://ha.example.test/api/webhook/gn")
    ntfy = respx.post("https://ntfy.example.test/").mock(return_value=httpx.Response(200))
    disc = respx.post("https://discord.example.test/api/webhooks/1/x").mock(return_value=httpx.Response(204))
    hook = respx.post("https://ha.example.test/api/webhook/gn").mock(return_value=httpx.Response(200))

    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    bo = Player(display_name="Bo")
    g = Game(name="Catan", scoring="high")
    db.add_all([bo, g])
    db.commit()
    payload = {"players": [{"player_id": ryan.id, "score": 10}, {"player_id": bo.id, "score": 7}]}
    r = client.post("/plays", data={"csrf": csrf, "game_id": str(g.id), "played_at": "2026-11-20T19:30",
                                    "payload": json.dumps(payload)}, follow_redirects=False)
    assert r.status_code == 303

    body = json.loads(ntfy.calls[0].request.content)
    assert body["topic"] == "gamenight" and body["title"] == "🏆 Ryan won Catan" and "Bo 7" in body["message"]
    assert ntfy.calls[0].request.headers["authorization"] == "Bearer tk"
    assert "Ryan won Catan" in json.loads(disc.calls[0].request.content)["content"]
    assert json.loads(hook.calls[0].request.content)["winners"] == ["Ryan"]


@respx.mock
def test_notification_failure_does_not_break_save(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "notify_webhook_url", "https://down.example.test/hook")
    respx.post("https://down.example.test/hook").mock(side_effect=httpx.ConnectError("boom"))
    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    g = Game(name="Solo", scoring="high")
    db.add(g)
    db.commit()
    r = client.post("/plays", data={"csrf": csrf, "game_id": str(g.id), "played_at": "2026-11-20T19:30",
                                    "payload": json.dumps({"players": [{"player_id": ryan.id, "score": 1}]})},
                    follow_redirects=False)
    assert r.status_code == 303
