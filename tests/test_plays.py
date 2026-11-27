import json

from sqlalchemy import select

from app.models import Game, Play, Player

from .conftest import login


def make_game(db, **kw):
    g = Game(name=kw.pop("name", "Catan"), scoring=kw.pop("scoring", "high"), **kw)
    db.add(g)
    db.commit()
    return g


def post_play(client, csrf, game_id, payload, url="/plays"):
    return client.post(url, data={"csrf": csrf, "game_id": str(game_id), "played_at": "2026-11-20T19:30",
                                  "location": "Home", "payload": json.dumps(payload)}, follow_redirects=False)


def test_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_create_play_with_rounds(client, db):
    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    sarah = Player(display_name="Sarah")
    db.add(sarah)
    db.commit()
    g = make_game(db, name="Hearts", scoring="low", uses_rounds=True)
    r = post_play(client, csrf, g.id, {
        "players": [{"player_id": ryan.id}, {"player_id": sarah.id}],
        "rounds": [{"scores": {str(ryan.id): 3, str(sarah.id): 10}, "won": [ryan.id]},
                   {"scores": {str(ryan.id): 8, str(sarah.id): 0}}],
    })
    assert r.status_code == 303, r.text
    db.expire_all()
    play = db.scalar(select(Play))
    seats = {s.player_id: s for s in play.seats}
    assert seats[ryan.id].score == 11 and seats[sarah.id].score == 10
    assert seats[sarah.id].is_winner and not seats[ryan.id].is_winner
    assert len(play.rounds) == 2 and play.rounds[0].scores
    page = client.get(f"/plays/{play.id}").text
    assert "Hearts" in page and "Sarah" in page
    assert client.get(f"/games/{g.id}").status_code == 200
    assert client.get(f"/players/{ryan.id}").status_code == 200
    assert client.get("/export/plays.csv").text.count("Hearts") == 2


def test_csrf_required(client, db):
    login(client)
    g = make_game(db)
    r = client.post("/plays", data={"game_id": str(g.id), "played_at": "2026-11-20T19:30",
                                    "payload": json.dumps({"players": [{"player_id": 1}]})})
    assert r.status_code == 400
    assert db.scalar(select(Play)) is None


def test_validation_error_rerenders_form(client, db):
    csrf = login(client)
    g = make_game(db)
    r = post_play(client, csrf, g.id, {"players": []})
    assert r.status_code == 422 and "Pick at least one player" in r.text


def test_only_participants_or_admin_can_edit(client, db):
    csrf = login(client, "Owner")
    owner = db.scalar(select(Player).where(Player.display_name == "Owner"))
    g = make_game(db)
    post_play(client, csrf, g.id, {"players": [{"player_id": owner.id, "score": 5}]})
    play_id = db.scalar(select(Play.id))

    outsider = Player(display_name="Outsider", is_admin=False)
    db.add(outsider)
    db.commit()
    client.post("/dev-login", data={"player_id": str(outsider.id)})
    assert client.get(f"/plays/{play_id}/edit").status_code == 403
    assert client.get("/admin").status_code == 403


def test_add_guest_via_json(client):
    csrf = login(client)
    r = client.post("/players", json={"name": "Uncle Bob"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["name"] == "Uncle Bob"
    r2 = client.post("/players", json={"name": "uncle bob"}, headers={"X-CSRF-Token": csrf})
    assert r2.json()["id"] == r.json()["id"] and r2.json()["existing"]
