import re
from datetime import datetime

from sqlalchemy import select

from app.models import Game, Play, PlaySeat, Player, Poll

from .conftest import csrf_of, login


def _vote(client, csrf, poll_id, option_id, on=True):
    return client.post(f"/polls/{poll_id}/vote", json={"option_id": option_id, "on": on},
                       headers={"X-CSRF-Token": csrf})


def test_vote_flow_tiebreak_and_permissions(client, db):
    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    sam = Player(display_name="Sam")
    recent, stale, never = Game(name="Recent"), Game(name="Stale"), Game(name="Never")
    db.add_all([sam, recent, stale, never])
    db.commit()
    for g, when in [(recent, datetime(2026, 11, 1)), (stale, datetime(2025, 1, 1))]:
        p = Play(game=g, played_at=when)
        p.seats.append(PlaySeat(player=ryan))
        db.add(p)
    db.commit()

    # picker renders the vote form with checkboxes
    assert 'name="game_ids"' in client.get("/pick").text
    r = client.post("/polls", data={"csrf": csrf, "title": "Friday?",
                                    "game_ids": [str(recent.id), str(stale.id), str(never.id)]},
                    follow_redirects=False)
    poll_id = int(r.headers["location"].rsplit("/", 1)[1])
    st = client.get(f"/polls/{poll_id}/state").json()
    opt = {o["name"]: o["id"] for o in st["options"]}
    # with no votes, never-played sorts first, then least recent
    assert [o["name"] for o in st["options"]] == ["Never", "Stale", "Recent"]

    st = _vote(client, csrf, poll_id, opt["Recent"]).json()
    st = _vote(client, csrf, poll_id, opt["Stale"]).json()
    assert {o["name"]: o["mine"] for o in st["options"]} == {"Recent": True, "Stale": True, "Never": False}

    # Sam votes too: Recent and Stale tie at 2, Stale played longer ago -> Stale leads
    client.post("/dev-login", data={"player_id": str(sam.id)})
    sam_csrf = csrf_of(client)
    _vote(client, sam_csrf, poll_id, opt["Recent"])
    st = _vote(client, sam_csrf, poll_id, opt["Stale"]).json()
    assert st["voters"] == 2 and st["options"][0]["name"] == "Stale" and st["options"][0]["votes"] == 2
    st = _vote(client, sam_csrf, poll_id, opt["Stale"], on=False).json()  # un-vote
    assert st["options"][0]["name"] == "Recent"
    assert client.post(f"/polls/{poll_id}/close", data={"csrf": sam_csrf}).status_code == 403

    client.post("/dev-login", data={"player_id": str(ryan.id)})
    csrf = csrf_of(client)
    client.post(f"/polls/{poll_id}/close", data={"csrf": csrf})
    db.expire_all()
    poll = db.get(Poll, poll_id)
    assert poll.closed_at and poll.winner.name == "Recent"
    assert "The table has spoken" in client.get(f"/polls/{poll_id}").text
    assert _vote(client, csrf, poll_id, opt["Never"]).status_code == 409
    assert "Friday?" in client.get("/polls").text


def test_needs_two_games(client, db):
    csrf = login(client)
    g = Game(name="Solo")
    db.add(g)
    db.commit()
    r = client.post("/polls", data={"csrf": csrf, "game_ids": [str(g.id)]}, follow_redirects=True)
    assert "at least two games" in r.text and db.scalar(select(Poll)) is None
