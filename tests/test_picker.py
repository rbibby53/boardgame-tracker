from datetime import datetime

from app.models import Game, Play, PlaySeat, Player

from .conftest import login


def test_picker_filters_and_sorts(client, db):
    login(client)
    p = Player(display_name="X")
    quick = Game(name="Quick", min_players=2, max_players=5, play_time_min=20)
    long = Game(name="Long", min_players=1, max_players=4, play_time_min=180)
    duo = Game(name="Duo", min_players=2, max_players=2)
    unknown = Game(name="Mystery")
    old = Game(name="Archived", archived=True)
    db.add_all([p, quick, long, duo, unknown, old])
    db.commit()
    pl = Play(game=quick, played_at=datetime(2026, 1, 1), duration_min=25)
    pl.seats.append(PlaySeat(player=p))
    db.add(pl)
    db.commit()

    html = client.get("/pick?players=4&minutes=60&sort=stale").text
    assert "Quick" in html and "Mystery" in html
    assert "Long" not in html and "Duo" not in html and "Archived" not in html
    # never-played games sort before played ones
    assert html.index("Mystery") < html.index("Quick")
    assert "Tonight's pick" in client.get("/pick?players=2&surprise=1").text
    # games with no player count / length are kept but flagged
    tight = client.get("/pick?players=9&minutes=10").text
    assert "Mystery" in tight and "player count not set" in tight and "Quick" not in tight
    db.delete(unknown)
    db.commit()
    assert "No games fit" in client.get("/pick?players=9&minutes=10").text
