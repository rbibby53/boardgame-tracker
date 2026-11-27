from datetime import datetime, timedelta

from app import fun
from app.models import Game, Play, PlaySeat, Player

from .conftest import login


def _series(db, g, a, b, c, results, start=datetime(2026, 3, 1, 19)):
    for i, win in enumerate(results):
        p = Play(game=g, played_at=start + timedelta(days=i * 7))
        order = {"a": [a, b, c], "b": [b, a, c], "c": [c, a, b]}[win]
        for place, pl in enumerate(order, 1):
            p.seats.append(PlaySeat(player=pl, score=10 - place, place=place, is_winner=place == 1))
        db.add(p)
    db.commit()
    return db.query(Play).all()


def test_streaks_rivals_awards_achievements(client, db):
    login(client)
    g = Game(name="Splendor", scoring="high")
    a, b, c = Player(display_name="Ann"), Player(display_name="Bo"), Player(display_name="Cy")
    db.add_all([g, a, b, c])
    db.commit()
    plays = _series(db, g, a, b, c, ["a", "a", "a", "b", "a", "a"])

    st = fun.streaks(plays)
    assert st[a.id]["best"] == 3 and st[a.id]["current"] == 2 and st[b.id]["current"] == 0

    r = fun.rivals(b.id, plays)
    assert r["nemesis"]["player"].id == a.id and r["victim"]["player"].id == c.id

    titles = {x["title"] for x in fun.awards(plays)}
    assert {"On a heater", "Longest win streak", "Always the bridesmaid", "Never misses a game"} <= titles

    got = {x.title for x in fun.achievements(a, plays)}
    assert {"First blood", "Hat trick", "Welcome to the table"} <= got
    assert "Participation trophy" in {x.title for x in fun.achievements(c, plays)}

    assert "Achievements" in client.get(f"/players/{a.id}").text
    assert "Awards" in client.get("/").text
