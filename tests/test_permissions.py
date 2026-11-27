import json

from sqlalchemy import select

from app import permissions
from app.models import Game, Player

from .conftest import csrf_of, login


def _as(client, db, name, groups=(), **kw):
    p = Player(display_name=name, source="sso", sub=f"sub-{name}", groups=json.dumps(list(groups)), **kw)
    db.add(p)
    db.commit()
    client.post("/dev-login", data={"player_id": str(p.id)})
    return p, csrf_of(client)


def _save(client, csrf, matrix=None, groups=None, default="player"):
    data = {"csrf": csrf, "default_role": default}
    for role, perms in (matrix or permissions.DEFAULT_MATRIX).items():
        for perm in perms:
            data[f"perm__{role}__{perm}"] = "1"
    for i, (g, r) in enumerate((groups or {}).items()):
        data[f"group_{i}"], data[f"grouprole_{i}"] = g, r
    return client.post("/admin/permissions", data=data, follow_redirects=True)


def test_defaults_viewer_groups_and_overrides(client, db):
    admin_csrf = login(client, "Admin")  # dev login = local admin
    admin = db.scalar(select(Player).where(Player.display_name == "Admin"))
    g = Game(name="Catan")
    db.add(g)
    db.commit()
    assert "Roles &amp; permissions" in client.get("/admin/permissions").text

    # Default role is player: can log & add games, can't archive or open admin
    pat, csrf = _as(client, db, "Pat")
    assert client.get("/plays/new").status_code == 200
    assert client.post(f"/games/{g.id}/archive", data={"csrf": csrf}).status_code == 403
    assert client.get("/admin").status_code == 403
    assert "+ Log play" in client.get("/").text

    # Make the default viewer and map a group to manager
    client.post("/dev-login", data={"player_id": str(admin.id)})
    r = _save(client, csrf_of(client), groups={"BG Managers": "manager"}, default="viewer")
    assert "Permissions saved" in r.text

    client.post("/dev-login", data={"player_id": str(pat.id)})
    csrf = csrf_of(client)
    assert client.get("/plays/new").status_code == 403
    assert client.post("/games", data={"csrf": csrf, "name": "X", "scoring": "high"}).status_code == 403
    assert client.get("/export/plays.csv").status_code == 403
    home = client.get("/").text
    assert "+ Log play" not in home and 'href="/admin"' not in home

    mo, csrf = _as(client, db, "Mo", groups=["BG Managers", "Other"])
    assert client.post(f"/games/{g.id}/archive", data={"csrf": csrf}, follow_redirects=False).status_code == 303

    # Per-person override beats the group mapping
    client.post("/dev-login", data={"player_id": str(admin.id)})
    client.post(f"/admin/permissions/user/{mo.id}", data={"csrf": csrf_of(client), "role": "viewer"})
    client.post("/dev-login", data={"player_id": str(mo.id)})
    assert client.post(f"/games/{g.id}/archive", data={"csrf": csrf_of(client)}).status_code == 403
    db.expire_all()
    page_role = permissions.role_source(db.get(Player, mo.id), permissions.load(db))
    assert page_role == ("viewer", "set here")


def test_cannot_remove_last_admin(client, db):
    login(client, "Boot")
    boot = db.scalar(select(Player).where(Player.display_name == "Boot"))
    # Hand admin to an SSO person via override, then retire the bootstrap admin
    sole, _ = _as(client, db, "Sole", role_override="admin")
    boot.is_admin = False
    boot.is_active = False
    db.commit()
    client.post("/dev-login", data={"player_id": str(sole.id)})
    r = client.post(f"/admin/permissions/user/{sole.id}", data={"csrf": csrf_of(client), "role": "player"},
                    follow_redirects=True)
    assert "nobody with admin" in r.text
    db.expire_all()
    assert db.get(Player, sole.id).role_override == "admin"


def test_login_records_groups(client, db, monkeypatch):
    from app import auth

    class Fake:
        async def authorize_access_token(self, request):
            return {"userinfo": {"sub": "g1", "preferred_username": "gina", "groups": ["Board Gamers", "Kids"]}}

        async def userinfo(self, token):
            return {}

    monkeypatch.setattr(auth, "_client", lambda: Fake())
    client.get("/auth/callback", follow_redirects=False)
    p = db.scalar(select(Player).where(Player.username == "gina"))
    assert permissions.groups_of(p) == ["Board Gamers", "Kids"]
