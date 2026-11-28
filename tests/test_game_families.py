import json
import re

from sqlalchemy import select

from app import collection
from app.models import Game, GameCategory, Play, Player

from .conftest import login


def make_pack(db):
    pack = Game(name="Jackbox Party Pack 7", owned=True, is_box=True)
    q = Game(name="Quiplash 3", scoring="high", min_players=3, max_players=8, play_time_min=20, owned=True)
    b = Game(name="Blather 'Round", scoring="winloss", min_players=2, max_players=6, play_time_min=20, owned=True)
    q.parent, b.parent = pack, pack
    solo = Game(name="Catan", min_players=3, max_players=4)
    db.add_all([pack, q, b, solo])
    db.commit()
    return pack, q, b, solo


def game_form(csrf, **kw):
    return {"csrf": csrf, "name": "X", "scoring": "high", **kw}


def test_add_mini_game_via_form_follows_collection_shelf(client, db):
    csrf = login(client)
    pack = Game(name="Jackbox Party Pack 8", owned=False, is_box=True)
    db.add(pack)
    db.commit()
    # "owned" is ticked, but the shelf is the collection's
    r = client.post("/games", data=game_form(csrf, name="Job Job", parent_id=str(pack.id), owned="on"),
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    jj = db.scalar(select(Game).where(Game.name == "Job Job"))
    assert jj.parent_id == pack.id and jj.owned is False
    # the "+ Add mini-game" link preselects the collection
    form = client.get(f"/games/new?parent={pack.id}").text
    assert re.search(rf'<option value="{pack.id}" selected>', form)


def test_nesting_rules(client, db):
    csrf = login(client)
    pack, q, _, solo = make_pack(db)
    # a mini-game can't be a collection
    r = client.post("/games", data=game_form(csrf, name="Deep", parent_id=str(q.id)))
    assert r.status_code == 422 and "is itself part of" in r.text
    # a collection can't join another collection
    r = client.post(f"/games/{pack.id}", data=game_form(csrf, name=pack.name, parent_id=str(solo.id), is_box="on"))
    assert r.status_code == 422 and "has its own mini-games" in r.text
    # a box can't be put in something else either, even without mini-games yet
    r = client.post("/games", data=game_form(csrf, name="New Box", parent_id=str(solo.id), is_box="on"))
    assert r.status_code == 422 and "A box of games can" in r.text
    # nor itself
    r = client.post(f"/games/{solo.id}", data=game_form(csrf, name="Catan", parent_id=str(solo.id)))
    assert r.status_code == 422 and "part of itself" in r.text
    # a duplicate name with a collection picked is a form error, not a crash
    r = client.post("/games", data=game_form(csrf, name="quiplash 3", parent_id=str(pack.id)))
    assert r.status_code == 422 and "already exists" in r.text
    # a collection's edit form explains instead of offering the picker
    assert "has 2 mini-games" in client.get(f"/games/{pack.id}/edit").text


def test_shelf_and_archive_cascade(client, db):
    csrf = login(client)
    pack, q, b, _ = make_pack(db)
    r = client.post(f"/games/{pack.id}", data=game_form(csrf, name=pack.name, is_box="on"),
                    follow_redirects=False)  # owned off
    assert r.status_code == 303
    db.expire_all()
    assert not q.owned and not b.owned
    # archiving one mini-game alone is fine
    client.post(f"/games/{b.id}/archive", data={"csrf": csrf})
    db.expire_all()
    assert b.archived and not q.archived and not pack.archived
    # archiving / restoring the collection takes the mini-games along
    client.post(f"/games/{pack.id}/archive", data={"csrf": csrf})
    db.expire_all()
    assert pack.archived and q.archived and b.archived
    client.post(f"/games/{pack.id}/archive", data={"csrf": csrf})
    db.expire_all()
    assert not pack.archived and not q.archived and not b.archived


def test_lists_pages_and_play_form(client, db):
    csrf = login(client, "Ryan")
    pack, q, b, solo = make_pack(db)
    html = client.get("/games").text
    # mini-games sit right under their collection, which has no Log button
    assert html.index("Jackbox Party Pack 7") < html.index("Blather") < html.index("Quiplash 3")
    assert "2 games" in html and f'/plays/new?game={pack.id}"' not in html
    # play form: the collection isn't playable, its mini-games are grouped under it
    form = client.get("/plays/new").text
    assert '<optgroup label="Jackbox Party Pack 7">' in form
    assert f'<option value="{pack.id}">' not in form and f'<option value="{q.id}">Quiplash 3</option>' in form
    boot = json.loads(re.search(r'id="boot"[^>]*>(.*?)</script>', form, re.S).group(1))
    assert pack.id not in {g["id"] for g in boot["games"]}

    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    r = client.post("/plays", data={"csrf": csrf, "game_id": str(q.id), "played_at": "2026-11-20T19:30",
                                    "payload": json.dumps({"players": [{"player_id": ryan.id, "score": 5000}]})},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    page = client.get(f"/games/{pack.id}").text
    assert "Box of games · 2 mini-games · 2–8 players" in page and "Mini-games" in page and "Wins over time" in page
    assert ">Rating<" not in page and "+ Add mini-game" in page
    assert "Part of" in client.get(f"/games/{q.id}").text
    # the collection's play filter includes its mini-games
    plays = client.get(f"/plays?game={pack.id}").text
    assert "Ryan · 5000" in plays
    assert "↳ Quiplash 3" in plays and "Jackbox Party Pack 7 (all)" in plays


def test_picker_suggests_mini_games_not_collections(client, db):
    login(client)
    pack, q, b, solo = make_pack(db)
    html = client.get("/pick?players=7").text
    assert "Quiplash 3" in html and "· Jackbox Party Pack 7" in html
    assert "Blather" not in html and "Catan" not in html  # 2-6 and 3-4 players
    assert f'href="/games/{pack.id}"' not in html


def test_delete_collection_frees_mini_games(client, db):
    csrf = login(client)
    pack, q, b, _ = make_pack(db)
    pack_id = pack.id
    r = client.post(f"/games/{pack_id}/delete", data={"csrf": csrf})
    assert "standalone games now" in r.text
    db.expunge(pack)
    db.expire_all()
    assert db.get(Game, pack_id) is None and q.parent_id is None and b.parent_id is None


def test_bgg_collection_sync_leaves_mini_games_to_their_collection(client, db):
    pack, q, b, solo = make_pack(db)
    solo.owned = True
    db.commit()
    gone = {g.name for g in collection.plan(db, [])["gone"]}
    assert gone == {"Jackbox Party Pack 7", "Catan"}
    collection.apply(db, [], add_new=False, unown_gone=True, archive_gone=False)
    db.expire_all()
    assert not pack.owned and not q.owned and not b.owned and not q.archived


def test_score_sheet_lines_survive_parent_changes(client, db):
    """Moving a game into a collection must not touch its score sheet or plays."""
    csrf = login(client)
    pack = Game(name="Pack", is_box=True)
    g = Game(name="Wingspan")
    g.categories.append(GameCategory(name="Birds"))
    db.add_all([pack, g])
    db.commit()
    r = client.post(f"/games/{g.id}", data=game_form(csrf, name="Wingspan", parent_id=str(pack.id),
                                                     categories="Birds"), follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert g.parent_id == pack.id and [c.name for c in g.categories] == ["Birds"]
    assert db.scalar(select(Play)) is None


def test_natural_sort_keeps_pack_numbers_in_order(client, db):
    login(client)
    for n in (1, 2, 10):
        p = Game(name=f"Jackbox Party Pack {n}", is_box=True)
        c = Game(name=f"Mini {n}")
        c.parent = p
        db.add_all([p, c])
    db.commit()
    html = client.get("/games").text
    assert html.index("Jackbox Party Pack 2<") < html.index("Jackbox Party Pack 10<")
    form = client.get("/plays/new").text
    assert form.index('label="Jackbox Party Pack 2"') < form.index('label="Jackbox Party Pack 10"')


def make_uno(db):
    uno = Game(name="Uno", min_players=2, max_players=10, owned=True)
    flip = Game(name="Uno Flip", min_players=2, max_players=10, owned=False)
    attack = Game(name="Uno Attack", min_players=2, max_players=10, owned=True)
    flip.parent, attack.parent = uno, uno
    db.add_all([uno, flip, attack])
    db.commit()
    return uno, flip, attack


def test_variants_keep_their_own_shelf_and_base_stays_playable(client, db):
    csrf = login(client, "Ryan")
    uno, flip, attack = make_uno(db)
    # editing the base doesn't touch its variants' shelf or archive status
    r = client.post(f"/games/{uno.id}", data=game_form(csrf, name="Uno"), follow_redirects=False)  # owned off
    assert r.status_code == 303
    client.post(f"/games/{uno.id}/archive", data={"csrf": csrf})
    db.expire_all()
    assert not uno.owned and uno.archived and attack.owned and not flip.owned and not attack.archived
    client.post(f"/games/{uno.id}/archive", data={"csrf": csrf})
    # a new variant keeps the shelf box it was given
    r = client.post("/games", data=game_form(csrf, name="Uno All Wild", parent_id=str(uno.id), owned="on"),
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.scalar(select(Game).where(Game.name == "Uno All Wild")).owned is True

    html = client.get("/games").text
    assert "3 variants" in html and f'/plays/new?game={uno.id}"' in html  # base is still loggable
    form = client.get("/plays/new").text
    group = form[form.index('<optgroup label="Uno">'):]
    group = group[:group.index("</optgroup>")]
    assert group.index(f'value="{uno.id}"') < group.index("Uno All Wild") < group.index("Uno Attack")
    pick = client.get("/pick?shelf=all").text
    assert f'href="/games/{uno.id}"' in pick and "· variant of Uno" in pick


def test_base_game_page_shows_variants_and_all_versions(client, db):
    csrf = login(client, "Ryan")
    uno, flip, attack = make_uno(db)
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    for g in (uno, flip, flip):
        r = client.post("/plays", data={"csrf": csrf, "game_id": str(g.id), "played_at": "2026-11-20T19:30",
                                        "payload": json.dumps({"players": [{"player_id": ryan.id, "score": 10}]})},
                        follow_redirects=False)
        assert r.status_code == 303, r.text
    page = client.get(f"/games/{uno.id}").text
    assert "Variants" in page and "+ Add variant" in page and "All versions" in page
    assert ">Rating<" in page  # the base keeps its own per-game columns
    assert "Variant of" in client.get(f"/games/{flip.id}").text
    assert client.get(f"/plays?game={uno.id}").text.count("Ryan · 10") == 3
    assert client.get(f"/plays?game={uno.id}&exact=1").text.count("Ryan · 10") == 1
    assert "Uno (all versions)" in client.get("/plays").text


def test_bgg_sync_treats_variants_as_their_own_boxes(client, db):
    uno, flip, attack = make_uno(db)
    gone = {g.name for g in collection.plan(db, [])["gone"]}
    assert gone == {"Uno", "Uno Attack"}
