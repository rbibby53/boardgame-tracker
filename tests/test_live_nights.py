"""Live game nights: start, join / leave, the pile, votes, live scoring with hand-over, saving, history, expiry."""
import json
import re
from datetime import timedelta

from sqlalchemy import select

from app import live as L
from app.models import Game, LiveNight, Play, Player, Poll, utcnow

from .conftest import csrf_of, login


def start(client, csrf, title="Game night at Mom's"):
    r = client.post("/live", data={"csrf": csrf, "title": title, "location": "Home"}, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


def as_user(client, name):
    client.cookies.clear()
    return login(client, name)


def games(db):
    mt = Game(name="Mexican Train", scoring="low", uses_rounds=True, min_players=2, max_players=8,
              round_names="Double 12\nDouble 11")
    uno = Game(name="Uno", min_players=2, max_players=10)
    duo = Game(name="Patchwork", min_players=2, max_players=2)
    db.add_all([mt, uno, duo])
    db.commit()
    return mt, uno, duo


def boot(html):
    return json.loads(re.search(r'id="boot"[^>]*>(.*?)</script>', html, re.S).group(1))


def test_start_join_leave_and_dashboard(client, db):
    csrf = login(client, "Mom")
    nid = start(client, csrf)
    night = db.get(LiveNight, nid)
    mom = db.scalar(select(Player).where(Player.display_name == "Mom"))
    assert night.host_id == mom.id and [m.player_id for m in night.here] == [mom.id]
    assert "Game night at Mom&#39;s" in client.get("/").text

    csrf = as_user(client, "Dad")
    home = client.get("/").text
    assert "Game night at Mom&#39;s" in home and ">Join<" in home
    client.post(f"/live/{nid}/join", data={"csrf": csrf})
    db.expire_all()
    assert len(db.get(LiveNight, nid).here) == 2
    # the bar on every other page while you're at a night
    assert "You're at" in client.get("/games").text
    r = client.post(f"/live/{nid}/leave", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    n = db.get(LiveNight, nid)
    assert len(n.here) == 1 and len(n.members) == 2  # Dad left but is remembered
    assert "You're at" not in client.get("/games").text
    client.post(f"/live/{nid}/join", data={"csrf": csrf})  # and comes back
    db.expire_all()
    assert len(db.get(LiveNight, nid).here) == 2


def test_two_nights_at_once_and_one_night_per_person(client, db):
    csrf = login(client, "Ryan")
    ryans = start(client, csrf, "Ryan's")
    csrf = as_user(client, "Mom")
    moms = start(client, csrf, "Mom & Dad's")
    assert {n.title for n in L.open_nights(db)} == {"Ryan's", "Mom & Dad's"}
    # Mom wanders over to Ryan's: she leaves her own night
    client.post(f"/live/{ryans}/join", data={"csrf": csrf})
    db.expire_all()
    mom = db.scalar(select(Player).where(Player.display_name == "Mom"))
    assert L.is_here(db.get(LiveNight, ryans), mom.id) and not L.is_here(db.get(LiveNight, moms), mom.id)


def test_people_and_guests_marked_here(client, db):
    csrf = login(client, "Mom")
    nid = start(client, csrf)
    grandma = Player(display_name="Grandma")
    db.add(grandma)
    db.commit()
    client.post(f"/live/{nid}/people", data={"csrf": csrf, "player_id": str(grandma.id)})
    client.post(f"/live/{nid}/people", data={"csrf": csrf, "guest": "Aunt Sue"})
    db.expire_all()
    names = {m.player.display_name for m in db.get(LiveNight, nid).here}
    assert names == {"Mom", "Grandma", "Aunt Sue"}


def test_pile_vote_and_up_next(client, db):
    csrf = login(client, "Mom")
    nid = start(client, csrf)
    mt, uno, duo = games(db)
    dad = Player(display_name="Dad")
    gran = Player(display_name="Grandma")
    db.add_all([dad, gran])
    db.commit()
    for p in (dad, gran):
        client.post(f"/live/{nid}/people", data={"csrf": csrf, "player_id": str(p.id)})
    client.post(f"/live/{nid}/pile", data={"csrf": csrf, "name": "mexican train"})
    r = client.post(f"/live/{nid}/vote", data={"csrf": csrf})
    assert "Add at least two games" in r.text  # only one game in the pile
    client.post(f"/live/{nid}/pile", data={"csrf": csrf, "game_id": str(uno.id)})
    client.post(f"/live/{nid}/pile", data={"csrf": csrf, "game_id": str(duo.id)})
    client.post(f"/live/{nid}/vote", data={"csrf": csrf})
    poll = db.scalar(select(Poll).where(Poll.live_night_id == nid))
    assert {o.game.name for o in poll.options} == {"Mexican Train", "Uno"}  # Patchwork is 2-player, 3 are here
    opt = next(o for o in poll.options if o.game_id == mt.id)
    r = client.post(f"/polls/{poll.id}/vote", json={"option_id": opt.id, "on": True}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    panel = client.get(f"/live/{nid}/panel").text
    assert 'data-poll="%d"' % poll.id in panel and "✓ Mexican Train" in panel
    r = client.post(f"/polls/{poll.id}/close", data={"csrf": csrf}, follow_redirects=False)
    assert r.headers["location"] == f"/live/{nid}"
    db.expire_all()
    assert db.get(LiveNight, nid).next_game_id == mt.id
    assert "Start Mexican Train" in client.get(f"/live/{nid}").text


def test_live_scoring_handover_and_save(client, db):
    csrf = login(client, "Mom")
    nid = start(client, csrf)
    mt, *_ = games(db)
    mom = db.scalar(select(Player).where(Player.display_name == "Mom"))
    dad = Player(display_name="Dad")
    db.add(dad)
    db.commit()
    client.post(f"/live/{nid}/people", data={"csrf": csrf, "player_id": str(dad.id)})
    r = client.post(f"/live/{nid}/start", data={"csrf": csrf, "game_id": str(mt.id)}, follow_redirects=False)
    assert r.headers["location"] == f"/plays/new?night={nid}"
    b = boot(client.get(f"/plays/new?night={nid}").text)
    assert b["live"]["night_id"] == nid
    assert {p["player_id"] for p in b["state"]["players"]} == {mom.id, dad.id}  # whoever's here
    v = b["live"]["version"]
    state = {**b["state"], "rounds": [{"label": "Double 12", "scores": {}, "won": [mom.id]}], "mode": "wins"}
    summary = {"mode": "who won each round", "lead": "After 1 round: Mom leads",
               "rows": [{"name": "Mom", "value": "1 round", "lead": True}, {"name": "Dad", "value": "0 rounds"}]}
    r = client.put(f"/live/{nid}/current", json={"version": v, "state": state, "fields": {"game_id": str(mt.id)},
                                                  "summary": summary}, headers={"X-CSRF-Token": csrf})
    assert r.json()["ok"] is True
    mom_csrf = csrf

    # Dad watches on his phone, then takes over
    dad_csrf = as_user(client, "Dad2")
    client.post(f"/live/{nid}/join", data={"csrf": dad_csrf})
    panel = client.get(f"/live/{nid}/panel").text
    assert "Now playing: Mexican Train" in panel and "After 1 round: Mom leads" in panel and "Keep score instead" in panel
    r = client.get(f"/plays/new?night={nid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/live/{nid}"  # not his to keep yet
    r = client.post(f"/live/{nid}/takeover", data={"csrf": dad_csrf}, follow_redirects=False)
    assert r.headers["location"] == f"/plays/new?night={nid}"
    b2 = boot(client.get(f"/plays/new?night={nid}").text)
    assert b2["state"]["rounds"][0]["won"] == [mom.id] and b2["live"]["version"] > v  # he picks up where Mom was
    dad2 = db.scalar(select(Player).where(Player.display_name == "Dad2"))

    # Mom's phone tries to save again: refused, and told who has it
    client.cookies.clear()
    r = client.post("/dev-login", data={"player_id": str(mom.id)}, follow_redirects=False)
    mom_csrf = csrf_of(client)
    r = client.put(f"/live/{nid}/current", json={"version": v, "state": state, "fields": {}, "summary": summary},
                   headers={"X-CSRF-Token": mom_csrf})
    assert r.status_code == 409 and r.json()["keeper"] == "Dad2"

    # Dad saves the finished game: it belongs to the night, the night moves on
    client.cookies.clear()
    client.post("/dev-login", data={"player_id": str(dad2.id)})
    dad_csrf = csrf_of(client)
    payload = {"players": [{"player_id": mom.id}, {"player_id": dad.id}, {"player_id": dad2.id}], "mode": "wins",
               "rounds": [{"label": "Double 12", "won": [mom.id]}, {"label": "Double 11", "won": [mom.id]}]}
    r = client.post("/plays", data={"csrf": dad_csrf, "game_id": str(mt.id), "played_at": "2026-11-27T20:00",
                                    "live_night_id": str(nid), "payload": json.dumps(payload)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/live/{nid}"
    db.expire_all()
    night = db.get(LiveNight, nid)
    play = db.scalar(select(Play))
    assert play.live_night_id == nid and night.current_game_id is None and night.current_state is None
    page = client.get(f"/live/{nid}").text
    assert "Tonight so far" in page and "Mom leading" in page.replace("\n", " ").replace("  ", " ") or "👑 Mom" in page

    # history: the night is its own entry, and the date page sends you to it
    assert "Game night at Mom&#39;s" in client.get("/nights").text
    r = client.get("/nights/2026-11-27", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/live/{nid}"
    assert f'href="/live/{nid}"' in client.get(f"/plays/{play.id}").text


def test_forgotten_nights_end_themselves(client, db):
    csrf = login(client, "Mom")
    nid = start(client, csrf)
    night = db.get(LiveNight, nid)
    night.last_activity_at = utcnow() - timedelta(hours=L.IDLE_HOURS, minutes=5)
    db.commit()
    assert L.open_nights(db) == []
    db.expire_all()
    night = db.get(LiveNight, nid)
    assert night.ended_reason == "idle" and night.ended_at is not None
    assert "This game night has ended" in client.get(f"/live/{nid}").text
    # and one started yesterday evening is closed by the night cutoff
    old = LiveNight(title="Yesterday", started_at=utcnow() - timedelta(days=1, hours=2),
                    last_activity_at=utcnow() - timedelta(minutes=5))
    db.add(old)
    db.commit()
    assert L.expire(db, old) and old.ended_reason == "cutoff"


def test_only_people_at_the_night_end_it(client, db):
    csrf = login(client, "Mom")
    nid = start(client, csrf)
    stranger = Player(display_name="Stranger", is_admin=False)  # dev-mode sign-ups are admins; this one isn't
    db.add(stranger)
    db.commit()
    client.cookies.clear()
    client.post("/dev-login", data={"player_id": str(stranger.id)})
    r = client.post(f"/live/{nid}/end", data={"csrf": csrf_of(client)})
    assert r.status_code == 403
    client.cookies.clear()
    mom = db.scalar(select(Player).where(Player.display_name == "Mom"))
    client.post("/dev-login", data={"player_id": str(mom.id)})
    client.post(f"/live/{nid}/end", data={"csrf": csrf_of(client)})
    db.expire_all()
    assert db.get(LiveNight, nid).ended_reason == "ended"
    assert "Game night at Mom" not in client.get("/").text.split("Latest plays")[0]
