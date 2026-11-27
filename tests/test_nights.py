from datetime import datetime

from app import nights
from app.models import Game, GameNight, Play, PlaySeat, Player

from .conftest import csrf_of, login


def test_night_grouping_and_page(client, db):
    login(client, "Ryan")
    g = Game(name="Hive", scoring="winloss")
    a, b = Player(display_name="Ann"), Player(display_name="Bo")
    db.add_all([g, a, b])
    db.commit()
    for when, winner in [(datetime(2026, 11, 20, 20, 0), a), (datetime(2026, 11, 21, 1, 30), a),
                         (datetime(2026, 11, 21, 19, 0), b)]:
        p = Play(game=g, played_at=when, winners_manual=True)
        p.seats += [PlaySeat(player=a, is_winner=winner is a), PlaySeat(player=b, is_winner=winner is b)]
        db.add(p)
    db.commit()

    grouped = nights.group(db.query(Play).all())
    assert [n.day.isoformat() for n in grouped] == ["2026-11-21", "2026-11-20"]
    fri = grouped[1]
    assert len(fri.plays) == 2 and [c.display_name for c in fri.champions] == ["Ann"]

    html = client.get("/nights/2026-11-20").text
    assert "Champion" in html and "Ann" in html
    assert client.get("/nights").status_code == 200
    assert client.get("/nights/2026-01-01").status_code == 404
    assert client.get("/nights/not-a-date").status_code == 404

    r = client.post("/nights/2026-11-20", data={"csrf": csrf_of(client), "title": "Friday Hive-off"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert db.query(GameNight).one().title == "Friday Hive-off"
    assert "Friday Hive-off" in client.get("/nights").text
