import io

import httpx
import pytest
import respx
from PIL import Image
from sqlalchemy import select

from app import bgg
from app.config import get_settings
from app.models import Game

from .conftest import csrf_of, login

SEARCH = """<?xml version="1.0" encoding="utf-8"?><items total="3">
<item type="boardgame" id="999"><name type="primary" value="Catan: Seafarers"/><yearpublished value="1997"/></item>
<item type="boardgame" id="13"><name type="primary" value="CATAN"/><yearpublished value="1995"/></item>
<item type="boardgame" id="5"><name type="alternate" value="Die Siedler von Catan"/></item></items>"""
THING = """<?xml version="1.0" encoding="utf-8"?><items><item type="boardgame" id="13">
<thumbnail>https://cf.geekdo-images.com/t.jpg</thumbnail><image>https://cf.geekdo-images.com/full.jpg</image>
<name type="alternate" sortindex="1" value="Die Siedler von Catan"/><name type="primary" sortindex="1" value="CATAN"/>
<yearpublished value="1995"/><minplayers value="3"/><maxplayers value="4"/>
<playingtime value="120"/><minplaytime value="60"/><maxplaytime value="120"/></item></items>"""


@pytest.fixture()
def token(monkeypatch):
    monkeypatch.setattr(get_settings(), "bgg_token", "bgg-tok")


@respx.mock
def test_search_sorts_exact_first_and_sends_token(token):
    route = respx.get("https://boardgamegeek.com/xmlapi2/search").mock(return_value=httpx.Response(200, text=SEARCH))
    res = bgg.search("catan")
    assert [r["id"] for r in res][:2] == [13, 999]
    assert route.calls[0].request.headers["authorization"] == "Bearer bgg-tok"


@respx.mock
def test_thing_parses_and_retries_when_queued(token):
    route = respx.get("https://boardgamegeek.com/xmlapi2/thing").mock(
        side_effect=[httpx.Response(202), httpx.Response(429), httpx.Response(200, text=THING)])
    slept = []
    g = bgg.thing(13, sleep=slept.append)
    assert g == {"id": 13, "name": "CATAN", "year": 1995, "min_players": 3, "max_players": 4,
                 "play_time_min": 120, "image": "https://cf.geekdo-images.com/full.jpg"}
    assert route.call_count == 3 and slept == [2.0, 4.0]


@respx.mock
def test_bad_token_and_unconfigured(token, monkeypatch):
    respx.get("https://boardgamegeek.com/xmlapi2/search").mock(return_value=httpx.Response(401))
    with pytest.raises(bgg.BggError, match="token"):
        bgg.search("catan")
    monkeypatch.setattr(get_settings(), "bgg_token", "")
    with pytest.raises(bgg.BggError, match="BGG_TOKEN"):
        bgg.search("catan")


def test_rejects_entity_bombs_and_foreign_image_hosts(token):
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><items>&b;</items>'
    with respx.mock:
        respx.get("https://boardgamegeek.com/xmlapi2/search").mock(return_value=httpx.Response(200, text=bomb))
        with pytest.raises(bgg.BggError):
            bgg.search("catan")
    with pytest.raises(bgg.BggError):
        bgg.fetch_image("https://evil.example.com/x.jpg")
    with pytest.raises(bgg.BggError):
        bgg.fetch_image("http://cf.geekdo-images.com/x.jpg")


@respx.mock
def test_create_game_with_bgg_cover(client, db, token):
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), (10, 120, 60)).save(buf, "JPEG")
    respx.get("https://boardgamegeek.com/xmlapi2/thing").mock(return_value=httpx.Response(200, text=THING))
    respx.get("https://cf.geekdo-images.com/full.jpg").mock(return_value=httpx.Response(200, content=buf.getvalue()))
    login(client)
    assert "Look up on BoardGameGeek" in client.get("/games/new").text
    r = client.post("/games", data={"csrf": csrf_of(client), "name": "CATAN", "scoring": "high",
                                    "bgg_id": "13", "bgg_cover": "1"}, follow_redirects=False)
    assert r.status_code == 303
    g = db.scalar(select(Game))
    assert g.has_cover and client.get(f"/covers/{g.id}").status_code == 200
    assert client.get("/api/bgg/thing/13").json()["name"] == "CATAN"
