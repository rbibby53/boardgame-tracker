"""Live game nights: who's here, the game pile, what's up next, the game being scored right now, and ending.

Timestamps on LiveNight are naive UTC (like created_at elsewhere). A night ends by hand, or by itself after
IDLE_HOURS without activity, or at the night cutoff (NIGHT_CUTOFF_HOUR the morning after it started), so a night
someone forgot to end doesn't sit on the dashboard. Several nights can be open at once; a person is in one at a time.
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import nights as N
from .config import get_settings
from .models import Game, LiveNight, LiveNightGame, LiveNightMember, Player, Poll, utcnow

IDLE_HOURS = 3


def _local(dt_utc: datetime) -> datetime:
    tz = ZoneInfo(get_settings().timezone)
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)


def _to_utc(dt_local: datetime) -> datetime:
    tz = ZoneInfo(get_settings().timezone)
    return dt_local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def night_day(night: LiveNight):
    """The evening this night belongs to (same rule as plays: before the cutoff hour counts as the night before)."""
    return N.night_of(_local(night.started_at))


def cutoff_utc(night: LiveNight) -> datetime:
    return _to_utc(N.bounds(night_day(night))[1])


def expire(db: Session, night: LiveNight, now: datetime | None = None) -> bool:
    """End a forgotten night. Returns True if it was ended now."""
    if not night.is_open:
        return False
    now = now or utcnow()
    reason = None
    if now >= cutoff_utc(night):
        reason = "cutoff"
    elif now - night.last_activity_at >= timedelta(hours=IDLE_HOURS):
        reason = "idle"
    if reason:
        end(night, reason, at=min(now, night.last_activity_at + timedelta(hours=IDLE_HOURS))
            if reason == "idle" else now)
        return True
    return False


def end(night: LiveNight, reason: str = "ended", at: datetime | None = None) -> None:
    night.ended_at = at or utcnow()
    night.ended_reason = reason
    night.current_game_id = night.current_keeper_id = night.current_state = None
    night.current_started_at = night.current_updated_at = None
    night.next_game_id = None


def _load_opts():
    return (selectinload(LiveNight.members).selectinload(LiveNightMember.player),
            selectinload(LiveNight.pile).selectinload(LiveNightGame.game),
            selectinload(LiveNight.host), selectinload(LiveNight.current_game), selectinload(LiveNight.next_game),
            selectinload(LiveNight.current_keeper))


def open_nights(db: Session) -> list[LiveNight]:
    """Open nights, newest first, after ending any that were forgotten."""
    rows = db.scalars(select(LiveNight).where(LiveNight.ended_at.is_(None)).options(*_load_opts())
                      .order_by(LiveNight.started_at.desc())).all()
    changed = [n for n in rows if expire(db, n)]
    if changed:
        db.commit()
    return [n for n in rows if n.is_open]


def get(db: Session, night_id: int) -> LiveNight | None:
    night = db.scalar(select(LiveNight).where(LiveNight.id == night_id).options(*_load_opts()))
    if night is not None and expire(db, night):
        db.commit()
    return night


def night_of_player(db: Session, player: Player) -> LiveNight | None:
    """The open night this person is at, if any."""
    for n in open_nights(db):
        if any(m.player_id == player.id for m in n.here):
            return n
    return None


def touch(night: LiveNight) -> None:
    night.last_activity_at = utcnow()


def member(night: LiveNight, player_id: int) -> LiveNightMember | None:
    return next((m for m in night.members if m.player_id == player_id), None)


def is_here(night: LiveNight, player_id: int) -> bool:
    m = member(night, player_id)
    return m is not None and m.left_at is None


def join(db: Session, night: LiveNight, player: Player) -> None:
    """Arrive (or come back). Someone at another open night leaves that one first."""
    for other in open_nights(db):
        if other.id != night.id and is_here(other, player.id):
            leave(other, player.id)
    m = member(night, player.id)
    if m is None:
        night.members.append(LiveNightMember(player_id=player.id, player=player))
    else:
        m.left_at = None
    touch(night)


def mark_here(night: LiveNight, player_id: int) -> None:
    """Someone who played a game here is here (without pulling them out of another night)."""
    m = member(night, player_id)
    if m is None:
        night.members.append(LiveNightMember(player_id=player_id))
    elif m.left_at is not None:
        m.left_at = None


def leave(night: LiveNight, player_id: int) -> None:
    m = member(night, player_id)
    if m is not None and m.left_at is None:
        m.left_at = utcnow()
        touch(night)


def add_to_pile(night: LiveNight, game: Game, by: Player) -> bool:
    if any(g.game_id == game.id for g in night.pile):
        return False
    night.pile.append(LiveNightGame(game_id=game.id, game=game, added_by_id=by.id))
    touch(night)
    return True


def fits(game: Game, n: int) -> bool:
    return (not game.min_players or n >= game.min_players) and (not game.max_players or n <= game.max_players)


def playable_pile(night: LiveNight) -> list[Game]:
    """Pile games you can actually start (a Jackbox pack in the pile stands for its mini-games)."""
    out: list[Game] = []
    for pg in night.pile:
        g = pg.game
        if g.archived:
            continue
        if g.is_box and g.children:
            out.extend(c for c in g.children if not c.archived and c not in out)
        elif g not in out:
            out.append(g)
    return sorted(out, key=lambda g: g.name.lower())


def open_vote(db: Session, night: LiveNight) -> Poll | None:
    return db.scalar(select(Poll).where(Poll.live_night_id == night.id, Poll.closed_at.is_(None))
                     .order_by(Poll.created_at.desc()))


def current(night: LiveNight) -> dict | None:
    """The game being scored right now: {state, fields, summary} as last saved by the scorekeeper's phone."""
    if not night.current_game_id:
        return None
    try:
        data = json.loads(night.current_state or "{}")
    except ValueError:
        data = {}
    return {"game": night.current_game, "keeper": night.current_keeper, "version": night.current_version,
            "started_at": night.current_started_at, "updated_at": night.current_updated_at,
            "state": data.get("state") or {}, "fields": data.get("fields") or {},
            "summary": data.get("summary") or {}}


def start_game(night: LiveNight, game: Game, keeper: Player) -> None:
    """Start scoring a game at this night: the players default to whoever's here."""
    night.current_game_id, night.current_game = game.id, game
    night.current_keeper_id, night.current_keeper = keeper.id, keeper
    night.current_version = (night.current_version or 0) + 1
    night.current_started_at = night.current_updated_at = utcnow()
    state = {"game_id": game.id, "players": [{"player_id": m.player_id} for m in night.here],
             "use_rounds": game.uses_rounds}
    night.current_state = json.dumps({"state": state, "fields": {"game_id": str(game.id)}, "summary": {}})
    if night.next_game_id == game.id:
        night.next_game_id = None
    touch(night)


def take_over(night: LiveNight, keeper: Player) -> None:
    night.current_keeper_id, night.current_keeper = keeper.id, keeper
    night.current_version = (night.current_version or 0) + 1
    touch(night)


def save_progress(night: LiveNight, keeper: Player, version: int, payload: dict) -> tuple[bool, int]:
    """Store the scorekeeper's latest form state. Refused (False) if someone else took over since."""
    if night.current_keeper_id != keeper.id or version != night.current_version:
        return False, night.current_version
    night.current_state = json.dumps({k: payload.get(k) for k in ("state", "fields", "summary")})[:200_000]
    night.current_updated_at = utcnow()
    touch(night)
    return True, night.current_version


def clear_current(night: LiveNight) -> None:
    night.current_game_id = night.current_keeper_id = night.current_state = None
    night.current_started_at = night.current_updated_at = None
    night.current_version = (night.current_version or 0) + 1
    touch(night)


def summary_night(night: LiveNight) -> N.Night:
    """This night's plays as a Night (standings, champion, sweep)."""
    return N.Night(night_day(night), list(night.plays), live=night)
