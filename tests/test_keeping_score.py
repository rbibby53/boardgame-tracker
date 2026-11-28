"""Round-winner scoring for points games, preset round names, expansions and favorites."""
import json
import re

from sqlalchemy import select

from app.models import Favorite, Game, GameExpansion, Play, Player

from .conftest import login


def boot(html: str) -> dict:
    return json.loads(re.search(r'id="boot"[^>]*>(.*?)</script>', html, re.S).group(1))


def family(db, names=("Dad", "Grandma")):
    out = [Player(display_name=n) for n in names]
    db.add_all(out)
    db.commit()
    return out


def post_play(client, csrf, game_id, payload):
    return client.post("/plays", data={"csrf": csrf, "game_id": str(game_id), "played_at": "2026-11-20T19:30",
                                       "payload": json.dumps(payload)}, follow_redirects=False)


def test_points_game_scored_by_round_wins(client, db):
    csrf = login(client, "Mom")
    mom = db.scalar(select(Player).where(Player.display_name == "Mom"))
    dad, gran = family(db)
    mt = Game(name="Mexican Train", scoring="low", uses_rounds=True,
              round_names="Double 12\nDouble 11\nDouble 10")
    db.add(mt)
    db.commit()
    ids = [mom.id, dad.id, gran.id]
    rounds = [{"label": "Double 12", "won": [mom.id]}, {"label": "Double 11", "won": [dad.id]},
              {"label": "Double 10", "won": [mom.id]}]
    # stray points are ignored in this mode: only who won each round counts
    rounds[0]["scores"] = {str(dad.id): 40}
    r = post_play(client, csrf, mt.id, {"players": [{"player_id": i} for i in ids], "rounds": rounds, "mode": "wins"})
    assert r.status_code == 303, r.text
    db.expire_all()
    play = db.scalar(select(Play))
    assert play.scoring == "winloss" and play.by_round_wins and play.scoring_label == "Most rounds won"
    seats = {s.player_id: s for s in play.seats}
    assert seats[mom.id].is_winner and not seats[dad.id].is_winner
    assert seats[mom.id].place == 1 and seats[dad.id].place == 2 and seats[gran.id].place == 3
    assert all(s.score is None for s in play.seats) and play.round_wins == {mom.id: 2, dad.id: 1}
    page = client.get(f"/plays/{play.id}").text
    assert "Most rounds won" in page and "Rounds won" in page and "Double 11" in page

    # the form remembers how this person keeps score for Mexican Train, and its round names
    g = next(x for x in boot(client.get("/plays/new").text)["games"] if x["id"] == mt.id)
    assert g["last_mode"] == "wins" and g["round_names"] == ["Double 12", "Double 11", "Double 10"]
    # editing keeps the mode
    assert boot(client.get(f"/plays/{play.id}/edit").text)["state"]["mode"] == "wins"


def test_points_mode_unchanged(client, db):
    csrf = login(client, "Mom")
    mom = db.scalar(select(Player).where(Player.display_name == "Mom"))
    (dad,) = family(db, ("Dad",))
    mt = Game(name="Mexican Train", scoring="low", uses_rounds=True)
    db.add(mt)
    db.commit()
    r = post_play(client, csrf, mt.id, {
        "players": [{"player_id": mom.id}, {"player_id": dad.id}], "mode": "points",
        "rounds": [{"scores": {str(mom.id): 0, str(dad.id): 12}, "won": [mom.id]},
                   {"scores": {str(mom.id): 30, str(dad.id): 0}, "won": [dad.id]}]})
    assert r.status_code == 303, r.text
    db.expire_all()
    play = db.scalar(select(Play))
    assert play.scoring is None and not play.by_round_wins
    seats = {s.player_id: s for s in play.seats}
    assert seats[mom.id].score == 30 and seats[dad.id].score == 12 and seats[dad.id].is_winner


def test_round_names_from_the_game_form(client, db):
    csrf = login(client)
    r = client.post("/games", data={"csrf": csrf, "name": "Mexican Train", "scoring": "low", "uses_rounds": "on",
                                    "round_names": "Double 12\n\n  Double 11  \nDouble blank\n"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    g = db.scalar(select(Game).where(Game.name == "Mexican Train"))
    assert g.round_name_list == ["Double 12", "Double 11", "Double blank"]


def test_expansions_owned_played_with_and_archived(client, db):
    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    (dad,) = family(db, ("Dad",))
    form = {"csrf": csrf, "name": "Catan", "scoring": "high", "exp_count": "4",
            "exp_name_0": "Seafarers", "exp_owned_0": "on", "exp_name_1": "5-6 Player Extension", "exp_owned_1": "on",
            "exp_name_2": "Cities & Knights", "exp_name_3": ""}
    assert client.post("/games", data=form, follow_redirects=False).status_code == 303
    catan = db.scalar(select(Game).where(Game.name == "Catan"))
    exps = {e.name: e for e in catan.expansions}
    assert [e.name for e in catan.expansions] == ["Seafarers", "5-6 Player Extension", "Cities & Knights"]
    assert exps["Seafarers"].owned and not exps["Cities & Knights"].owned

    sea, ext = exps["Seafarers"], exps["5-6 Player Extension"]
    r = post_play(client, csrf, catan.id, {"players": [{"player_id": ryan.id, "score": 10}, {"player_id": dad.id, "score": 8}],
                                          "expansions": [ext.id, sea.id]})
    assert r.status_code == 303, r.text
    play = db.scalar(select(Play))
    assert [e.name for e in play.played_with] == ["Seafarers", "5-6 Player Extension"]
    assert "Played with" in client.get(f"/plays/{play.id}").text
    page = client.get(f"/games/{catan.id}").text
    assert "Expansions" in page and "played with 1 time" in page and "not played yet" in page

    # next time, the form pre-selects what this person played with last time
    g = next(x for x in boot(client.get("/plays/new").text)["games"] if x["id"] == catan.id)
    assert sorted(g["last_expansions"]) == sorted([sea.id, ext.id]) and len(g["expansions"]) == 3

    # removing a used expansion archives it (old plays keep it); an unused one is deleted
    form = {"csrf": csrf, "name": "Catan", "scoring": "high", "exp_count": "3",
            "exp_id_0": str(sea.id), "exp_name_0": "", "exp_id_1": str(ext.id), "exp_name_1": "5-6 Player Extension",
            "exp_owned_1": "on", "exp_id_2": str(exps["Cities & Knights"].id), "exp_name_2": ""}
    assert client.post(f"/games/{catan.id}", data=form, follow_redirects=False).status_code == 303
    db.expire_all()
    left = {e.name: e.archived for e in db.scalars(select(GameExpansion)).all()}
    assert left == {"Seafarers": True, "5-6 Player Extension": False}
    assert {e.name for e in db.get(Play, play.id).played_with} == {"Seafarers", "5-6 Player Extension"}

    # an expansion of another game is refused
    other = Game(name="Carcassonne")
    other.expansions.append(GameExpansion(name="Inns & Cathedrals"))
    db.add(other)
    db.commit()
    r = post_play(client, csrf, catan.id, {"players": [{"player_id": ryan.id, "score": 9}],
                                          "expansions": [other.expansions[0].id]})
    assert r.status_code == 422 and "don&#39;t belong to this game" in r.text


def test_favorites(client, db):
    csrf = login(client, "Mom")
    mom = db.scalar(select(Player).where(Player.display_name == "Mom"))
    mt, catan = Game(name="Mexican Train", min_players=2, max_players=8), Game(name="Catan")
    pack = Game(name="Jackbox Party Pack 7", is_box=True)
    q3 = Game(name="Quiplash 3")
    q3.parent = pack
    db.add_all([mt, catan, pack, q3])
    db.commit()
    for g in (mt, pack):
        r = client.post(f"/games/{g.id}/favorite", data={"csrf": csrf, "next": "/games"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/games"
    assert {f.game_id for f in db.scalars(select(Favorite).where(Favorite.player_id == mom.id))} == {mt.id, pack.id}

    fav_list = client.get("/games?fav=true").text
    assert "Mexican Train" in fav_list and "Catan" not in fav_list
    form = client.get("/plays/new").text
    group = form[form.index('label="★ Your favorites"'):]
    group = group[:group.index("</optgroup>")]
    assert "Mexican Train" in group and "Quiplash 3" in group and "Catan" not in group  # a starred box brings its games
    pick = client.get("/pick?shelf=fav").text
    assert "Mexican Train" in pick and "Quiplash 3" in pick and "Catan" not in pick
    # favorites are per person
    login(client, "Dad")
    assert "Mexican Train" not in client.get("/games?fav=true").text
    # toggling again removes it (JSON for scripts)
    client.cookies.clear()
    csrf = login(client, "Mom2")
    r = client.post(f"/games/{mt.id}/favorite", data={"csrf": csrf}, headers={"Accept": "application/json"})
    assert r.json() == {"favorite": True}
    r = client.post(f"/games/{mt.id}/favorite", data={"csrf": csrf}, headers={"Accept": "application/json"})
    assert r.json() == {"favorite": False}


def test_search_boxes_present(client, db):
    login(client)
    db.add(Game(name="Uno"))
    db.commit()
    assert 'id="gameSearch"' in client.get("/games").text
    assert 'id="gameFilter"' in client.get("/plays/new").text
