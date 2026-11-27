import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ.update({
    "SECRET_KEY": "t" * 40,
    "DEV_MODE": "true",
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "BASE_URL": "http://testserver",
    "AUTHENTIK_URL": "https://auth.example.test",
    "AUTHENTIK_TOKEN": "tok",
    "AUTHENTIK_GROUP": "Game Night",
    "OIDC_ADMIN_GROUP": "Game Admins",
    "AUTHENTIK_SYNC_INTERVAL_MINUTES": "0",
    "DATA_DIR": f"{_tmp}/data",
    "BACKUP_HOUR": "3",
})
os.makedirs(f"{_tmp}/data", exist_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, get_engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:  # runs migrations via lifespan
        yield c
    engine = get_engine()
    with engine.begin() as conn:
        for t in reversed(Base.metadata.sorted_tables):
            conn.execute(t.delete())
    from app import permissions

    permissions.invalidate()


@pytest.fixture()
def db(client):
    from app.db import SessionLocal

    with SessionLocal() as s:
        yield s


def csrf_of(client) -> str:
    import re

    html = client.get("/players").text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(client, name="Tester"):
    r = client.post("/dev-login", data={"new_name": name}, follow_redirects=False)
    assert r.status_code == 303
    return csrf_of(client)
