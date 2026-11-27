"""Scheduled SQLite backups to DATA_DIR/backups (gzipped, rotated).

Uses SQLite's online backup API, so it's consistent while the app is running.
Postgres deployments should use pg_dump instead; photos/covers live in DATA_DIR and
are covered by backing up that directory.
"""

import gzip
import logging
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import get_settings
from .db import get_engine

log = logging.getLogger(__name__)
NAME = re.compile(r"^boardgames-\d{8}-\d{6}\.db\.gz$")


class BackupError(Exception):
    pass


def backup_dir() -> Path:
    d = Path(get_settings().data_dir) / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path() -> Path:
    s = get_settings()
    if not s.is_sqlite:
        raise BackupError("Built-in backups cover SQLite only. For Postgres, schedule pg_dump.")
    db = get_engine().url.database
    if not db or db == ":memory:":
        raise BackupError("No SQLite file to back up.")
    return Path(db)


def run_backup() -> Path:
    src_path = _db_path()
    stamp = datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y%m%d-%H%M%S")
    dest = backup_dir() / f"boardgames-{stamp}.db.gz"
    with tempfile.TemporaryDirectory(dir=backup_dir()) as tmp:
        raw = Path(tmp) / "snapshot.db"
        src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
        dst = sqlite3.connect(raw)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        ok = sqlite3.connect(raw).execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise BackupError(f"Integrity check failed on snapshot: {ok}")
        with open(raw, "rb") as fin, gzip.open(dest.with_suffix(".tmp"), "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
        dest.with_suffix(".tmp").replace(dest)
    prune()
    log.info("Backup written: %s", dest.name)
    return dest


def list_backups() -> list[dict]:
    out = []
    for p in sorted(backup_dir().glob("boardgames-*.db.gz"), reverse=True):
        if NAME.match(p.name):
            st = p.stat()
            out.append({"name": p.name, "size": st.st_size, "when": datetime.fromtimestamp(st.st_mtime)})
    return out


def prune() -> None:
    keep = max(1, get_settings().backup_keep)
    for b in list_backups()[keep:]:
        (backup_dir() / b["name"]).unlink(missing_ok=True)


def path_for(name: str) -> Path | None:
    if not NAME.match(name):
        return None
    p = backup_dir() / name
    return p if p.is_file() else None


def seconds_until_next() -> float:
    s = get_settings()
    tz = ZoneInfo(s.timezone)
    now = datetime.now(tz)
    nxt = now.replace(hour=s.backup_hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()
