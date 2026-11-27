import gzip
import sqlite3

from app import backup
from app.config import get_settings
from app.models import Game

from .conftest import csrf_of, login


def test_backup_now_download_and_prune(client, db, monkeypatch, tmp_path):
    login(client, "Ryan")
    db.add(Game(name="Root", scoring="high"))
    db.commit()
    for b in backup.list_backups():
        (backup.backup_dir() / b["name"]).unlink()

    r = client.post("/admin/backup", data={"csrf": csrf_of(client)}, follow_redirects=False)
    assert r.status_code == 303
    files = backup.list_backups()
    assert len(files) == 1
    dl = client.get(f"/admin/backups/{files[0]['name']}")
    assert dl.status_code == 200
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(dl.content))
    assert sqlite3.connect(restored).execute("select name from games").fetchall() == [("Root",)]
    assert "Backups" in client.get("/admin").text

    assert client.get("/admin/backups/..%2Fboardgames.db").status_code == 404
    assert backup.path_for("../../etc/passwd") is None

    monkeypatch.setattr(get_settings(), "backup_keep", 2)
    for i in range(3):
        (backup.backup_dir() / f"boardgames-2020010{i}-000000.db.gz").write_bytes(b"x")
    backup.prune()
    assert len(backup.list_backups()) == 2


def test_seconds_until_next_is_within_a_day():
    assert 0 < backup.seconds_until_next() <= 86400
