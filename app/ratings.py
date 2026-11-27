"""Skill ratings with OpenSkill (Plackett-Luce), recomputed from the full play history.

Display rating = 1000 + 40 * ordinal, where ordinal = mu - 3*sigma (a conservative
estimate). A brand-new player starts at exactly 1000 and moves as results come in.
Co-op plays and plays with no ranking information (everyone tied) are not rated.
"""

import threading
from dataclasses import dataclass, field

from openskill.models import PlackettLuce
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Play

PROVISIONAL_PLAYS = 5
_model = PlackettLuce()


def display(r) -> int:
    return round(1000 + 40 * r.ordinal())


@dataclass
class RatingTable:
    overall: dict[int, object] = field(default_factory=dict)  # player_id -> openskill rating
    per_game: dict[tuple[int, int], object] = field(default_factory=dict)  # (game_id, player_id)
    rated_plays: dict[int, int] = field(default_factory=dict)
    rated_game_plays: dict[tuple[int, int], int] = field(default_factory=dict)
    # play_id -> {player_id: (overall_after, game_after)}
    after: dict[int, dict[int, tuple[int, int]]] = field(default_factory=dict)

    def overall_for(self, pid: int) -> int | None:
        r = self.overall.get(pid)
        return display(r) if r else None

    def game_for(self, gid: int, pid: int) -> int | None:
        r = self.per_game.get((gid, pid))
        return display(r) if r else None

    def provisional(self, pid: int, gid: int | None = None) -> bool:
        n = self.rated_game_plays.get((gid, pid), 0) if gid else self.rated_plays.get(pid, 0)
        return n < PROVISIONAL_PLAYS


def ranking(play: Play) -> list[tuple[list[int], int]] | None:
    """[(player_ids_in_team, rank)] with rank 1 = best, or None if the play can't be rated."""
    if play.game.scoring == "coop" or len(play.seats) < 2:
        return None
    if any(s.place is not None for s in play.seats):
        worst = max(s.place for s in play.seats if s.place is not None) + 1
        rank = {s.player_id: (s.place if s.place is not None else worst) for s in play.seats}
        # A manually picked winner outranks the score order.
        if play.winners_manual:
            for s in play.seats:
                rank[s.player_id] = 1 if s.is_winner else rank[s.player_id] + 1
    else:
        rank = {s.player_id: (1 if s.is_winner else 2) for s in play.seats}

    teams: dict[str, list[int]] = {}
    for s in play.seats:
        key = f"team:{s.team.lower()}" if s.team else f"solo:{s.player_id}"
        teams.setdefault(key, []).append(s.player_id)
    out = [(ids, min(rank[i] for i in ids)) for ids in teams.values()]
    if len(out) < 2 or len({r for _, r in out}) < 2:
        return None
    return out


def _rate(table: dict, key_fn, teams: list[tuple[list[int], int]]):
    current = [[table.get(key_fn(pid)) or _model.rating() for pid in ids] for ids, _ in teams]
    new = _model.rate(current, ranks=[r for _, r in teams])
    for (ids, _), team in zip(teams, new):
        for pid, r in zip(ids, team):
            table[key_fn(pid)] = r


def build(plays: list[Play]) -> RatingTable:
    t = RatingTable()
    for play in sorted(plays, key=lambda p: (p.played_at, p.id)):
        teams = ranking(play)
        if not teams:
            continue
        _rate(t.overall, lambda pid: pid, teams)
        _rate(t.per_game, lambda pid, g=play.game_id: (g, pid), teams)
        t.after[play.id] = {}
        for ids, _ in teams:
            for pid in ids:
                t.rated_plays[pid] = t.rated_plays.get(pid, 0) + 1
                k = (play.game_id, pid)
                t.rated_game_plays[k] = t.rated_game_plays.get(k, 0) + 1
                t.after[play.id][pid] = (display(t.overall[pid]), display(t.per_game[k]))
    return t


_lock = threading.Lock()
_cache: tuple[tuple, RatingTable] | None = None


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def table(db: Session, all_plays: list[Play] | None = None) -> RatingTable:
    """Cached on (play count, newest edit, newest id) so it only recomputes after a change.
    Pass `all_plays` only if you already loaded every play (never a filtered list)."""
    global _cache
    key = tuple(db.execute(select(func.count(Play.id), func.max(Play.updated_at), func.max(Play.id))).one())
    with _lock:
        if _cache and _cache[0] == key:
            return _cache[1]
    if all_plays is None:
        from .stats import load_plays

        all_plays = load_plays(db)
    t = build(all_plays)
    with _lock:
        _cache = (key, t)
    return t


def series(t: RatingTable, plays: list[Play], *, game_id: int | None = None,
           only_player: int | None = None) -> list[dict]:
    """Rating after each rated play, per player, for a line chart."""
    from .stats import color_for

    out: dict[int, dict] = {}
    for play in plays:
        if game_id is not None and play.game_id != game_id:
            continue
        snap = t.after.get(play.id)
        if not snap:
            continue
        for s in play.seats:
            if s.player_id not in snap or (only_player is not None and s.player_id != only_player):
                continue
            d = out.setdefault(s.player_id, {"label": s.player.display_name, "color": color_for(s.player),
                                             "data": []})
            val = snap[s.player_id][1 if game_id is not None else 0]
            d["data"].append({"x": play.played_at.isoformat(), "y": val, "play": play.id, "win": s.is_winner})
    return list(out.values())
