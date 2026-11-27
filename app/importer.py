"""Import plays from CSV (this app's export format or a spreadsheet in that layout) or BGG play XML.

Flow: parse -> preview (dry run, nothing written) -> confirm (writes). The parsed batch is kept
in DATA_DIR/imports/<token>.json between the two steps.
"""

import csv
import io
from collections import Counter
import json
import secrets
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import bgg
from .config import get_settings
from .models import Game, Play, PlaySeat, Player
from .scoring import PlayPayload, compute

MAX_PLAYS = 20_000
DEFAULT_TIME = time(19, 0)  # BGG only records a date; assume an evening game


class ImportError_(ValueError):
    pass


@dataclass
class ISeat:
    name: str
    username: str | None = None
    score: float | None = None
    win: bool = False
    team: str | None = None


@dataclass
class IPlay:
    key: str
    game: str
    played_at: str  # ISO
    bgg_id: int | None = None
    location: str | None = None
    duration_min: int | None = None
    expansions: str | None = None
    notes: str | None = None
    seats: list[ISeat] = field(default_factory=list)


# ---------------------------------------------------------------- parsing

TRUE = {"1", "true", "yes", "y", "x", "win", "won", "w"}


def _num(v) -> float | None:
    v = (v or "").strip().replace(",", "") if isinstance(v, str) else v
    if v in ("", None, "—", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ImportError_(f"'{v}' isn't a number") from None


def _when(v: str) -> datetime:
    v = (v or "").strip()
    for fmt in (None, "%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.fromisoformat(v) if fmt is None else datetime.strptime(v, fmt)
        except ValueError:
            continue
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        if len(v) <= 10:
            dt = datetime.combine(dt.date(), DEFAULT_TIME)
        return dt
    raise ImportError_(f"'{v}' isn't a date (use YYYY-MM-DD or YYYY-MM-DD HH:MM)")


def parse_csv(text: str) -> list[IPlay]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise ImportError_("The CSV is empty.")
    cols = {c.strip().lower(): c for c in rows[0].keys() if c}
    need = {"game", "player"}
    date_col = cols.get("played_at") or cols.get("date")
    if not need <= set(cols) or not date_col:
        raise ImportError_("CSV needs at least these columns: played_at (or date), game, player.")

    def get(row, name):
        c = cols.get(name)
        return (row.get(c) or "").strip() if c else ""

    plays: dict[str, IPlay] = {}
    for n, row in enumerate(rows, 2):
        try:
            game, player = get(row, "game"), get(row, "player")
            if not game or not player:
                continue
            when = _when((row.get(date_col) or "").strip())
            key = get(row, "play_id") or f"{when.isoformat()}|{game.lower()}"
            p = plays.get(key)
            if p is None:
                dur = _num(get(row, "duration_min"))
                p = plays[key] = IPlay(key=key, game=game, played_at=when.isoformat(),
                                       location=get(row, "location") or None,
                                       duration_min=int(dur) if dur else None,
                                       expansions=get(row, "expansions") or None, notes=get(row, "notes") or None)
            p.seats.append(ISeat(name=player, username=get(row, "username") or None, score=_num(get(row, "score")),
                                 win=get(row, "winner").lower() in TRUE, team=get(row, "team") or None))
        except ImportError_ as e:
            raise ImportError_(f"Row {n}: {e}") from None
    return list(plays.values())


def parse_bgg_xml(data: bytes) -> list[IPlay]:
    try:
        root = SafeET.fromstring(data)
    except (ET.ParseError, DefusedXmlException) as e:
        raise ImportError_("That isn't a BoardGameGeek plays XML file.") from e
    return _parse_bgg_root(root)


def _parse_bgg_root(root: ET.Element) -> list[IPlay]:
    if root.tag != "plays":
        raise ImportError_("That isn't a BoardGameGeek plays XML file.")
    out = []
    for play in root.findall("play"):
        item = play.find("item")
        if item is None or play.get("incomplete") == "1":
            continue
        seats = []
        for pl in play.findall("players/player"):
            name = (pl.get("name") or pl.get("username") or "").strip()
            if not name or name.lower() == "anonymous player":
                continue
            score = pl.get("score")
            try:
                score_v = float(score) if score not in (None, "") else None
            except ValueError:
                score_v = None
            seats.append(ISeat(name=name, username=(pl.get("username") or None), score=score_v,
                               win=pl.get("win") == "1"))
        if not seats:
            continue
        length = int(play.get("length") or 0) or None
        date = play.get("date") or ""
        try:
            when = datetime.combine(datetime.strptime(date, "%Y-%m-%d").date(), DEFAULT_TIME)
        except ValueError:
            continue
        qty = max(1, int(play.get("quantity") or 1))
        for i in range(qty):
            out.append(IPlay(key=f"bgg:{play.get('id')}:{i}", game=item.get("name") or "Unknown game",
                             bgg_id=int(item.get("objectid")) if (item.get("objectid") or "").isdigit() else None,
                             played_at=when.isoformat(), location=play.get("location") or None,
                             duration_min=length, notes=(play.findtext("comments") or "").strip() or None,
                             seats=[ISeat(**asdict(s)) for s in seats]))
    return out


def fetch_bgg_user(username: str) -> list[IPlay]:
    """All logged plays for a BGG user via XML API2 (100 per page)."""
    plays: list[IPlay] = []
    page = 1
    while True:
        root = bgg._get("plays", {"username": username, "page": str(page), "type": "thing"})
        chunk = _parse_bgg_root(root)
        plays += chunk
        total = int(root.get("total") or 0)
        if not root.findall("play") or page * 100 >= total or len(plays) > MAX_PLAYS:
            break
        page += 1
    if not plays:
        raise ImportError_(f"No plays found for BGG user '{username}' (or their plays are private).")
    return plays


# ---------------------------------------------------------------- staging

def _dir() -> Path:
    d = Path(get_settings().data_dir) / "imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stage(plays: list[IPlay], source: str) -> str:
    if len(plays) > MAX_PLAYS:
        raise ImportError_(f"Too many plays in one import (max {MAX_PLAYS}).")
    token = secrets.token_hex(16)
    (_dir() / f"{token}.json").write_text(json.dumps({"source": source, "plays": [asdict(p) for p in plays]}))
    return token


def load(token: str) -> tuple[str, list[IPlay]]:
    if not token.isalnum() or len(token) != 32:
        raise ImportError_("Import not found. Upload it again.")
    p = _dir() / f"{token}.json"
    if not p.is_file():
        raise ImportError_("Import expired or already applied. Upload it again.")
    d = json.loads(p.read_text())
    return d["source"], [IPlay(**{**x, "seats": [ISeat(**s) for s in x["seats"]]}) for x in d["plays"]]


def discard(token: str) -> None:
    if token.isalnum():
        (_dir() / f"{token}.json").unlink(missing_ok=True)


# ---------------------------------------------------------------- matching / applying

def _player_index(db: Session) -> tuple[dict, dict]:
    by_user, by_name = {}, {}
    for p in db.scalars(select(Player)).all():
        if p.username:
            by_user[p.username.lower()] = p
        by_name.setdefault(p.display_name.lower(), p)
    return by_user, by_name


def _match_player(seat: ISeat, by_user: dict, by_name: dict) -> Player | None:
    if seat.username and seat.username.lower() in by_user:
        return by_user[seat.username.lower()]
    return by_name.get(seat.name.lower())


def _match_game(db: Session, ip: IPlay) -> Game | None:
    if ip.bgg_id:
        g = db.scalar(select(Game).where(Game.bgg_id == ip.bgg_id))
        if g:
            return g
    return db.scalar(select(Game).where(func.lower(Game.name) == ip.game.lower()))


def _fingerprint(when: datetime, game_id: int | None, names: list[str]) -> tuple:
    return (when.date(), game_id, tuple(sorted(n.lower() for n in names)))


def _existing_fingerprints(db: Session) -> Counter:
    """How many plays already exist per (day, game, players). Two real games of the same thing on the
    same night are both kept; re-importing the same file skips them again."""
    fps: Counter = Counter()
    for p in db.scalars(select(Play).options(selectinload(Play.seats).selectinload(PlaySeat.player))).all():
        fps[_fingerprint(p.played_at, p.game_id, [s.player.display_name for s in p.seats])] += 1
    return fps


def preview(db: Session, plays: list[IPlay]) -> dict:
    by_user, by_name = _player_index(db)
    existing = _existing_fingerprints(db)
    new_games: dict[str, int] = {}
    new_players: dict[str, int] = {}
    matched: dict[int, list] = {}
    dupes = 0
    for ip in plays:
        g = _match_game(db, ip)
        names = []
        for s in ip.seats:
            m = _match_player(s, by_user, by_name)
            if m:
                matched.setdefault(m.id, [m, 0])[1] += 1
                names.append(m.display_name)
            else:
                new_players[s.name] = new_players.get(s.name, 0) + 1
                names.append(s.name)
        if g is None:
            new_games[ip.game] = new_games.get(ip.game, 0) + 1
        else:
            fp = _fingerprint(datetime.fromisoformat(ip.played_at), g.id, names)
            if existing[fp] > 0:
                existing[fp] -= 1
                dupes += 1
    dates = sorted(p.played_at for p in plays)
    return {
        "plays": len(plays), "dupes": dupes, "new_games": sorted(new_games.items()),
        "new_players": sorted(new_players.items(), key=lambda kv: -kv[1]),
        "matched": sorted(matched.values(), key=lambda v: -v[1]),
        "first": dates[0][:10] if dates else None, "last": dates[-1][:10] if dates else None,
    }


def apply(db: Session, plays: list[IPlay], user: Player) -> dict:
    by_user, by_name = _player_index(db)
    existing = _existing_fingerprints(db)
    games: dict[str, Game] = {}
    created = {"plays": 0, "games": 0, "players": 0, "skipped": 0}

    for ip in plays:
        when = datetime.fromisoformat(ip.played_at)
        g = _match_game(db, ip) or games.get(ip.game.lower())
        if g is None:
            has_scores = any(s.score is not None for s in ip.seats)
            g = Game(name=ip.game[:200], scoring="high" if has_scores else "winloss", bgg_id=ip.bgg_id)
            db.add(g)
            db.flush()
            games[ip.game.lower()] = g
            created["games"] += 1

        seat_players: list[tuple[ISeat, Player]] = []
        for s in ip.seats:
            p = _match_player(s, by_user, by_name)
            if p is None:
                p = Player(display_name=s.name[:150], source="local")
                db.add(p)
                db.flush()
                by_name[p.display_name.lower()] = p
                created["players"] += 1
            if all(p.id != q.id for _, q in seat_players):
                seat_players.append((s, p))

        fp = _fingerprint(when, g.id, [p.display_name for _, p in seat_players])
        if existing[fp] > 0:
            existing[fp] -= 1
            created["skipped"] += 1
            continue

        payload = PlayPayload(players=[{"player_id": p.id, "score": s.score, "winner": s.win, "team": s.team}
                                       for s, p in seat_players],
                              winners_manual=True, coop_won=None)
        play = Play(game_id=g.id, played_at=when, location=(ip.location or None) and ip.location[:200],
                    duration_min=ip.duration_min, expansions=ip.expansions and ip.expansions[:500],
                    notes=ip.notes, winners_manual=True, created_by_id=user.id)
        for r in compute(g.scoring if g.scoring != "coop" else "high", payload):
            play.seats.append(PlaySeat(player_id=r.player_id, score=r.score, score_manual=r.score is not None,
                                       place=r.place, is_winner=r.is_winner, team=r.team))
        if g.scoring == "coop":
            play.coop_won = any(s.win for s, _ in seat_players)
            play.winners_manual = False
            for seat in play.seats:
                seat.is_winner, seat.place = bool(play.coop_won), None
        db.add(play)
        created["plays"] += 1
    db.commit()
    return created
