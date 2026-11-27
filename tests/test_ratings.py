import json
from datetime import datetime, timedelta

from sqlalchemy import select

from app import ratings
from app.models import Game, Play, PlaySeat, Player

from .conftest import login


def _setup(db, scoring="high"):
    g = Game(name=f"G-{scoring}", scoring=scoring)
    ps = [Player(display_name=n) for n in ("A", "B", "C", "D")]
    db.add_all([g, *ps])
    db.commit()
    return g, ps


def _play(db, g, seats, when, **kw):
    p = Play(game=g, played_at=when, **kw)
    for pl, score, place, win, team in seats:
        p.seats.append(PlaySeat(player=pl, score=score, place=place, is_winner=win, team=team))
    db.add(p)
    db.commit()
    return p


def test_winner_up_loser_down_and_ties(client, db):
    g, (a, b, c, _) = _setup(db)
    t0 = datetime(2026, 1, 1)
    _play(db, g, [(a, 10, 1, True, None), (b, 10, 1, True, None), (c, 2, 3, False, None)], t0)
    t = ratings.build(db.scalars(select(Play)).all())
    assert t.overall_for(a.id) == t.overall_for(b.id) > 1000 > t.overall_for(c.id)
    assert t.game_for(g.id, a.id) == t.overall_for(a.id)
    assert t.provisional(a.id)


def test_unrateable_plays_skipped(client, db):
    g, (a, b, *_) = _setup(db, "coop")
    g2 = Game(name="Tie", scoring="high")
    db.add(g2)
    db.commit()
    _play(db, g, [(a, None, None, True, None), (b, None, None, True, None)], datetime(2026, 1, 1), coop_won=True)
    _play(db, g2, [(a, 5, 1, True, None), (b, 5, 1, True, None)], datetime(2026, 1, 2))
    t = ratings.build(db.scalars(select(Play)).all())
    assert t.overall == {}


def test_teams_move_together(client, db):
    g, (a, b, c, d) = _setup(db, "winloss")
    _play(db, g, [(a, None, None, True, "Red"), (b, None, None, True, "red"),
                  (c, None, None, False, "Blue"), (d, None, None, False, "Blue")], datetime(2026, 1, 1))
    t = ratings.build(db.scalars(select(Play)).all())
    assert t.overall_for(a.id) == t.overall_for(b.id) > t.overall_for(c.id) == t.overall_for(d.id)


def test_cache_refreshes_after_score_only_edit(client, db):
    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    sam = Player(display_name="Sam")
    g = Game(name="Catan", scoring="high")
    db.add_all([sam, g])
    db.commit()

    def save(url, ry, sm):
        payload = {"players": [{"player_id": ryan.id, "score": ry}, {"player_id": sam.id, "score": sm}]}
        r = client.post(url, data={"csrf": csrf, "game_id": str(g.id), "played_at": "2026-11-20T19:30",
                                   "payload": json.dumps(payload)}, follow_redirects=False)
        assert r.status_code == 303, r.text
        return r.headers["location"]

    loc = save("/plays", 10, 5)
    before = ratings.table(db).overall_for(ryan.id)
    assert before > 1000
    save(loc, 5, 10)  # same play, same row values, only seat scores change
    db.expire_all()
    after = ratings.table(db).overall_for(ryan.id)
    assert after < 1000
    assert "Rating" in client.get("/").text
    assert client.get(f"/players/{ryan.id}").status_code == 200
    assert client.get(f"/games/{g.id}").status_code == 200
