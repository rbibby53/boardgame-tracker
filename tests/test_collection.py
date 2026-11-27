import io
import re

import httpx
import pytest
import respx
from PIL import Image
from sqlalchemy import select

from app import bgg, collection
from app.config import get_settings
from app.models import Game

from .conftest import csrf_of, login

COLL = """<?xml version="1.0" encoding="utf-8"?>
<items totalitems="4" termsofuse="https://boardgamegeek.com/xmlapi/termsofuse">
 <item objecttype="thing" objectid="13" subtype="boardgame" collid="1">
  <name sortindex="1">CATAN</name><yearpublished>1995</yearpublished>
  <image>https://cf.geekdo-images.com/catan.jpg</image><thumbnail>https://cf.geekdo-images.com/catan_t.jpg</thumbnail>
  <stats minplayers="3" maxplayers="4" minplaytime="60" maxplaytime="120" playingtime="120" numowned="1">
   <rating value="8"/></stats>
  <status own="1" prevowned="0" fortrade="0" want="0" wanttoplay="0" wanttobuy="0" wishlist="0" preordered="0"/>
  <numplays>4</numplays>
 </item>
 <item objecttype="thing" objectid="13" subtype="boardgame" collid="2">
  <name sortindex="1">CATAN</name><stats minplayers="3" maxplayers="4" playingtime="120"/><status own="1"/>
 </item>
 <item objecttype="thing" objectid="230802" subtype="boardgame" collid="3">
  <name sortindex="1">Azul</name><yearpublished>2017</yearpublished>
  <stats minplayers="2" maxplayers="4" playingtime="45"/><status own="1"/>
 </item>
 <item objecttype="thing" objectid="999" subtype="boardgameexpansion" collid="4">
  <name sortindex="1">Some Expansion</name><stats minplayers="1" maxplayers="1" playingtime="1"/><status own="1"/>
 </item>
</items>"""
URL = "https://boardgamegeek.com/xmlapi2/collection"


@pytest.fixture()
def token(monkeypatch):
    monkeypatch.setattr(get_settings(), "bgg_token", "bgg-tok")


@respx.mock
def test_collection_parse_waits_through_202(token):
    route = respx.get(URL).mock(side_effect=[httpx.Response(202), httpx.Response(202),
                                             httpx.Response(200, text=COLL)])
    slept = []
    items = bgg.collection("ryan", sleep=slept.append)
    assert [i["name"] for i in items] == ["Azul", "CATAN"]  # dupes collapsed, expansion skipped
    catan = items[1]
    assert (catan["bgg_id"], catan["min_players"], catan["max_players"], catan["play_time_min"], catan["year"]) == \
        (13, 3, 4, 120, 1995)
    q = route.calls[0].request.url.params
    assert q["own"] == "1" and q["excludesubtype"] == "boardgameexpansion" and q["username"] == "ryan"
    assert slept == [2, 3]


@respx.mock
def test_collection_user_error(token):
    respx.get(URL).mock(return_value=httpx.Response(
        200, text="<errors><error><message>Invalid username specified</message></error></errors>"))
    with pytest.raises(bgg.BggError, match="Invalid username"):
        bgg.collection("nobody-xyz")


@respx.mock
def test_sync_preview_and_apply(client, db, token):
    buf = io.BytesIO()
    Image.new("RGB", (500, 500), (200, 50, 50)).save(buf, "JPEG")
    respx.get(URL).mock(return_value=httpx.Response(200, text=COLL))
    respx.get("https://cf.geekdo-images.com/catan.jpg").mock(return_value=httpx.Response(200, content=buf.getvalue()))
    csrf = login(client, "Ryan")
    # existing: Catan by name with a hand-set play time, plus a game we no longer own
    db.add_all([Game(name="Catan", play_time_min=90), Game(name="Sold Game")])
    db.commit()

    r = client.post("/admin/collection", data={"csrf": csrf, "username": "ryan"})
    assert r.status_code == 200 and "owns 2 board games" in r.text and "Sold Game" in r.text
    tok = re.search(r"/admin/collection/([0-9a-f]{32})/apply", r.text).group(1)
    assert db.scalar(select(Game).where(Game.name == "Azul")) is None  # preview wrote nothing

    r = client.post(f"/admin/collection/{tok}/apply",
                    data={"csrf": csrf_of(client), "add_new": "1", "gone": "unown", "covers": "1"},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    catan = db.scalar(select(Game).where(Game.name == "Catan"))
    assert catan.bgg_id == 13 and catan.play_time_min == 90  # hand edit kept
    assert (catan.min_players, catan.max_players, catan.year) == (3, 4, 1995)  # blanks filled
    assert catan.has_cover  # background task ran in the test client
    azul = db.scalar(select(Game).where(Game.name == "Azul"))
    assert azul.owned and azul.bgg_id == 230802 and azul.play_time_min == 45
    sold = db.scalar(select(Game).where(Game.name == "Sold Game"))
    assert not sold.owned and not sold.archived
    assert collection.get_username(db) == "ryan"

    # picker now defaults to the shelf and hides the sold game
    html = client.get("/pick").text
    assert "Azul" in html and "Sold Game" not in html
    assert "Sold Game" in client.get("/pick?shelf=all").text

    # re-sync is a no-op for the matched games
    r = client.post("/admin/collection", data={"csrf": csrf_of(client), "username": "ryan"})
    assert "<strong>0</strong> new game" in r.text
