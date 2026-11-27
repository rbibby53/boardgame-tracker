import json
import re

from sqlalchemy import func, select

from app.models import Game, Play, PlaySeat, Player

from .conftest import csrf_of, login

BGG_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<plays username="ryan" userid="1" total="3" page="1">
 <play id="101" date="2025-12-24" quantity="2" length="45" incomplete="0" nowinstats="0" location="Mom's">
  <item name="Ticket to Ride" objecttype="thing" objectid="9209"/>
  <comments>Christmas Eve</comments>
  <players>
   <player username="ryan" userid="1" name="Ryan" score="112" win="1"/>
   <player username="" userid="0" name="Grandma" score="98" win="0"/>
  </players>
 </play>
 <play id="102" date="2025-12-26" quantity="1" length="0" incomplete="1">
  <item name="Ticket to Ride" objecttype="thing" objectid="9209"/>
  <players><player name="Ryan" win="1"/></players>
 </play>
</plays>"""


def _preview(client, files=None, data=None):
    r = client.post("/admin/import", data={"csrf": csrf_of(client), **(data or {})}, files=files)
    token = re.search(r"/admin/import/([0-9a-f]{32})/apply", r.text)
    return r, token.group(1) if token else None


def test_csv_roundtrip_through_export(client, db):
    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    bo = Player(display_name="Bo")
    g = Game(name="Catan", scoring="high")
    db.add_all([bo, g])
    db.commit()
    for when, rs, bs in [("2026-11-01T19:00", 10, 7), ("2026-11-08T19:00", 6, 9)]:
        client.post("/plays", data={"csrf": csrf, "game_id": str(g.id), "played_at": when, "location": "Home",
                                    "payload": json.dumps({"players": [{"player_id": ryan.id, "score": rs},
                                                                       {"player_id": bo.id, "score": bs}]})})
    csv_text = client.get("/export/plays.csv").text

    # Same file again: everything is a duplicate
    r, token = _preview(client, files={"file": ("plays.csv", csv_text.encode(), "text/csv")})
    assert "Skip <strong>2</strong>" in r.text
    client.post(f"/admin/import/{token}/apply", data={"csrf": csrf_of(client)})
    assert db.scalar(select(func.count(Play.id))) == 2

    # Wipe and re-import: same results come back
    for p in db.scalars(select(Play)).all():
        db.delete(p)
    db.commit()
    r, token = _preview(client, files={"file": ("plays.csv", csv_text.encode(), "text/csv")})
    assert db.scalar(select(func.count(Play.id))) == 0  # preview writes nothing
    client.post(f"/admin/import/{token}/apply", data={"csrf": csrf_of(client)})
    plays = db.scalars(select(Play).order_by(Play.played_at)).all()
    assert len(plays) == 2 and plays[0].location == "Home"
    winners = [next(s.player.display_name for s in p.seats if s.is_winner) for p in plays]
    assert winners == ["Ryan", "Bo"]
    assert db.scalar(select(func.count(Player.id))) == 2  # matched, no new guests
    # token is single-use
    r = client.post(f"/admin/import/{token}/apply", data={"csrf": csrf_of(client)})
    assert "expired" in r.text or r.status_code == 200


def test_bgg_xml_import(client, db):
    login(client, "Ryan")
    r, token = _preview(client, files={"file": ("plays.xml", BGG_XML, "text/xml")})
    assert token and "Grandma" in r.text and "Ticket to Ride" in r.text
    client.post(f"/admin/import/{token}/apply", data={"csrf": csrf_of(client)})
    plays = db.scalars(select(Play)).all()
    assert len(plays) == 2  # quantity=2, incomplete play skipped
    g = db.scalar(select(Game))
    assert g.name == "Ticket to Ride" and g.bgg_id == 9209 and g.scoring == "high"
    p = plays[0]
    assert p.location == "Mom's" and p.duration_min == 45 and p.played_at.hour == 19
    seats = {s.player.display_name: s for s in db.scalars(select(PlaySeat).where(PlaySeat.play_id == p.id))}
    assert seats["Ryan"].is_winner and seats["Ryan"].score == 112 and seats["Grandma"].place == 2
    assert db.scalar(select(Player).where(Player.display_name == "Grandma")).source == "local"

    # Re-importing the same file is a no-op (both same-day games are recognized)
    r, token = _preview(client, files={"file": ("plays.xml", BGG_XML, "text/xml")})
    assert "Skip <strong>2</strong>" in r.text
    client.post(f"/admin/import/{token}/apply", data={"csrf": csrf_of(client)})
    assert len(db.scalars(select(Play)).all()) == 2


def test_import_errors_and_permissions(client, db):
    login(client, "Admin")
    r, token = _preview(client, files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert r.status_code == 422 and "columns" in r.text and token is None
    r, _ = _preview(client, files={"file": ("x.csv", b"date,game,player,score\nnope,Catan,Ann,1\n", "text/csv")})
    assert "Row 2" in r.text
    nobody = Player(display_name="Nobody")
    db.add(nobody)
    db.commit()
    client.post("/dev-login", data={"player_id": str(nobody.id)})
    assert client.get("/admin/import").status_code == 403
