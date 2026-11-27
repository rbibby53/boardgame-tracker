import httpx
import pytest
import respx
from sqlalchemy import select

from app.authentik import SyncError, run_sync
from app.auth import upsert_from_claims
from app.config import get_settings
from app.models import Player, SyncLog

URL = "https://auth.example.test/api/v3/core/users/"


def ak_user(pk, username, name, groups=(), uid=None, avatar=""):
    return {"pk": pk, "username": username, "name": name, "email": f"{username}@example.test",
            "uid": uid or f"hash-{pk}", "uuid": f"uuid-{pk}", "avatar": avatar, "is_superuser": False,
            "groups_obj": [{"name": g} for g in groups], "attributes": {}}


def page(results, nxt=0):
    return httpx.Response(200, json={"pagination": {"next": nxt, "current": 1}, "results": results})


@respx.mock
def test_sync_paginates_creates_and_sets_admin(db):
    route = respx.get(URL).mock(side_effect=[
        page([ak_user(1, "ryan", "Ryan B", groups=["Game Night", "Game Admins"],
                      avatar="https://gravatar.test/a.png")], nxt=2),
        page([ak_user(2, "sarah", "Sarah", groups=["Game Night"])]),
    ])
    res = run_sync(db, get_settings())
    assert (res.created, res.updated, res.total) == (2, 0, 2)
    q = route.calls[0].request.url.params
    assert q.get_list("groups_by_name") == ["Game Night"]
    assert set(q.get_list("type")) == {"internal", "external"}
    ryan = db.scalar(select(Player).where(Player.username == "ryan"))
    assert ryan.sub == "hash-1" and ryan.is_admin and ryan.avatar_url.startswith("https://")
    assert not db.scalar(select(Player).where(Player.username == "sarah")).is_admin


@respx.mock
def test_sync_deactivates_removed_users_but_keeps_guests(db):
    db.add(Player(display_name="Guest", source="local"))
    db.commit()
    respx.get(URL).mock(return_value=page([ak_user(1, "ryan", "Ryan"), ak_user(2, "sarah", "Sarah")]))
    run_sync(db, get_settings())
    respx.get(URL).mock(return_value=page([ak_user(1, "ryan", "Ryan")]))
    res = run_sync(db, get_settings())
    assert res.deactivated == 1
    assert not db.scalar(select(Player).where(Player.username == "sarah")).is_active
    assert db.scalar(select(Player).where(Player.display_name == "Guest")).is_active


@respx.mock
def test_empty_result_does_not_wipe_roster(db):
    respx.get(URL).mock(return_value=page([ak_user(1, "ryan", "Ryan")]))
    run_sync(db, get_settings())
    respx.get(URL).mock(return_value=page([]))
    with pytest.raises(SyncError):
        run_sync(db, get_settings())
    assert db.scalar(select(Player).where(Player.username == "ryan")).is_active
    assert db.scalars(select(SyncLog).order_by(SyncLog.id.desc())).first().ok is False


@respx.mock
def test_auth_error_is_clear(db):
    respx.get(URL).mock(return_value=httpx.Response(403, json={"detail": "nope"}))
    with pytest.raises(SyncError, match="403"):
        run_sync(db, get_settings())


@respx.mock
def test_login_links_to_synced_player(db):
    respx.get(URL).mock(return_value=page([ak_user(7, "mike", "Mike", groups=["Game Night"])]))
    run_sync(db, get_settings())
    p = upsert_from_claims(db, {"sub": "hash-7", "preferred_username": "mike", "name": "Mike T",
                                "email": "mike@example.test", "groups": ["Game Night", "Game Admins"]})
    assert p.username == "mike" and p.display_name == "Mike T" and p.is_admin
    assert len(db.scalars(select(Player)).all()) == 1


def test_callback_route_logs_in(client, monkeypatch):
    from app import auth

    class FakeClient:
        async def authorize_access_token(self, request):
            return {"userinfo": {"sub": "abc", "preferred_username": "jen", "name": "Jen", "groups": []}}

        async def userinfo(self, token):
            return {"email": "jen@example.test"}

    monkeypatch.setattr(auth, "_client", lambda: FakeClient())
    r = client.get("/auth/callback", follow_redirects=False)
    assert r.status_code == 303
    assert "Jen" in client.get("/").text
