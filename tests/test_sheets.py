import json

from sqlalchemy import select

from app.models import Game, GameCategory, Play, Player

from .conftest import login


def _game(client, csrf, name, cats, scoring="high", game_id=None):
    url = f"/games/{game_id}" if game_id else "/games"
    r = client.post(url, data={"csrf": csrf, "name": name, "scoring": scoring, "categories": cats},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_score_sheet_totals_and_archiving(client, db):
    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    sarah = Player(display_name="Sarah")
    db.add(sarah)
    db.commit()
    gid = _game(client, csrf, "7 Wonders", "Military\nCoins\nScience\ncoins")  # dupes ignored
    cats = db.scalars(select(GameCategory).where(GameCategory.game_id == gid).order_by(GameCategory.position)).all()
    assert [c.name for c in cats] == ["Military", "Coins", "Science"]
    mil, coins, sci = (c.id for c in cats)

    payload = {"players": [{"player_id": ryan.id}, {"player_id": sarah.id}],
               "categories": {str(mil): {str(ryan.id): 9, str(sarah.id): 3},
                              str(coins): {str(ryan.id): 4, str(sarah.id): 6},
                              str(sci): {str(sarah.id): 13}}}
    r = client.post("/plays", data={"csrf": csrf, "game_id": str(gid), "played_at": "2026-11-20T19:30",
                                    "payload": json.dumps(payload)}, follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    play = db.scalar(select(Play))
    seats = {s.player_id: s for s in play.seats}
    assert seats[ryan.id].score == 13 and seats[sarah.id].score == 22 and seats[sarah.id].is_winner
    assert len(play.category_scores) == 5
    assert "Score sheet" in client.get(f"/plays/{play.id}").text
    assert "Score sheet averages" in client.get(f"/games/{gid}").text

    # Edit form round-trips the sheet
    assert f'"{mil}": {{"{ryan.id}": 9.0' in client.get(f"/plays/{play.id}/edit").text.replace("\\u0022", '"')

    # Removing a used line archives it; removing an unused line deletes it
    _game(client, csrf, "7 Wonders", "Military\nCoins\nGuilds", game_id=gid)
    db.expire_all()
    g = db.get(Game, gid)
    by = {c.name: c for c in g.categories}
    assert by["Science"].archived and not by["Guilds"].archived
    assert [c.name for c in g.active_categories] == ["Military", "Coins", "Guilds"]


def test_sheet_rejects_foreign_category(client, db):
    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    g1 = _game(client, csrf, "A", "X")
    g2 = _game(client, csrf, "B", "Y")
    other = db.scalar(select(GameCategory).where(GameCategory.game_id == g2))
    payload = {"players": [{"player_id": ryan.id}], "categories": {str(other.id): {str(ryan.id): 1}}}
    r = client.post("/plays", data={"csrf": csrf, "game_id": str(g1), "played_at": "2026-11-20T19:30",
                                    "payload": json.dumps(payload)})
    assert r.status_code == 422 and "score sheet" in r.text.lower()
