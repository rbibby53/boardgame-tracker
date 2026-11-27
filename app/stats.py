"""Aggregations computed in Python. Fine for a household/club-sized dataset (thousands of plays)."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Game, Play, PlaySeat, Player


def color_for(p: Player) -> str:
    return p.display_color


def load_plays(db: Session, *, game_id: int | None = None, player_id: int | None = None,
               since: datetime | None = None) -> list[Play]:
    q = (
        select(Play)
        .options(
            selectinload(Play.game),
            selectinload(Play.seats).selectinload(PlaySeat.player),
            selectinload(Play.category_scores),
        )
        .order_by(Play.played_at, Play.id)
    )
    if game_id is not None:
        q = q.where(Play.game_id == game_id)
    if player_id is not None:
        q = q.where(Play.seats.any(PlaySeat.player_id == player_id))
    if since is not None:
        q = q.where(Play.played_at >= since)
    return list(db.scalars(q).all())


@dataclass
class Row:
    player: Player
    plays: int = 0
    wins: int = 0
    scores: list[float] = field(default_factory=list)
    places: list[int] = field(default_factory=list)
    last_played: datetime | None = None

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.plays if self.plays else None

    @property
    def avg_score(self) -> float | None:
        return round(sum(self.scores) / len(self.scores), 1) if self.scores else None

    @property
    def avg_place(self) -> float | None:
        return round(sum(self.places) / len(self.places), 2) if self.places else None

    def best(self, scoring: str) -> float | None:
        if not self.scores:
            return None
        return min(self.scores) if scoring == "low" else max(self.scores)


def leaderboard(plays: list[Play], *, include_coop: bool = False) -> list[Row]:
    rows: dict[int, Row] = {}
    for play in plays:
        if play.game.scoring == "coop" and not include_coop:
            continue
        for seat in play.seats:
            r = rows.setdefault(seat.player_id, Row(seat.player))
            r.plays += 1
            r.wins += int(seat.is_winner)
            if seat.score is not None:
                r.scores.append(seat.score)
            if seat.place is not None:
                r.places.append(seat.place)
            r.last_played = play.played_at
    return sorted(rows.values(), key=lambda r: (-r.wins, -(r.win_rate or 0), -r.plays, r.player.display_name))


def records(game: Game, plays: list[Play]) -> dict:
    best = None
    margin = None
    for play in plays:
        scored = [s for s in play.seats if s.score is not None]
        for s in scored:
            if best is None or (s.score < best[0] if game.scoring == "low" else s.score > best[0]):
                best = (s.score, s.player, play)
        if game.scoring in ("high", "low") and len(scored) >= 2:
            ordered = sorted(scored, key=lambda s: s.score, reverse=game.scoring == "high")
            diff = abs(ordered[0].score - ordered[1].score)
            if margin is None or diff > margin[0]:
                margin = (diff, ordered[0].player, play)
    durations = [p.duration_min for p in plays if p.duration_min]
    return {
        "best": best,
        "margin": margin,
        "avg_duration": round(sum(durations) / len(durations)) if durations else None,
        "coop_wins": sum(1 for p in plays if p.coop_won),
    }


def score_series(plays: list[Play]) -> list[dict]:
    """Final score per player per play, for a line chart."""
    by_player: dict[int, dict] = {}
    for play in plays:
        for s in play.seats:
            if s.score is None:
                continue
            d = by_player.setdefault(s.player_id, {
                "label": s.player.display_name, "color": color_for(s.player), "data": [],
            })
            d["data"].append({"x": play.played_at.isoformat(), "y": s.score, "play": play.id,
                              "win": s.is_winner})
    return list(by_player.values())


def cumulative_wins_series(plays: list[Play], *, only_player: int | None = None) -> list[dict]:
    totals: dict[int, int] = defaultdict(int)
    out: dict[int, dict] = {}
    for play in plays:
        for s in play.seats:
            if only_player is not None and s.player_id != only_player:
                continue
            if s.is_winner and play.game.scoring != "coop":
                totals[s.player_id] += 1
                d = out.setdefault(s.player_id, {
                    "label": s.player.display_name, "color": color_for(s.player), "data": [],
                })
                d["data"].append({"x": play.played_at.isoformat(), "y": totals[s.player_id], "play": play.id})
    return list(out.values())


def per_game_for_player(player_id: int, plays: list[Play]) -> list[dict]:
    games: dict[int, dict] = {}
    for play in plays:
        seat = next((s for s in play.seats if s.player_id == player_id), None)
        if seat is None:
            continue
        g = games.setdefault(play.game_id, {"game": play.game, "plays": 0, "wins": 0, "scores": [],
                                            "last": None})
        g["plays"] += 1
        g["wins"] += int(seat.is_winner)
        if seat.score is not None:
            g["scores"].append(seat.score)
        g["last"] = play.played_at
    rows = []
    for g in games.values():
        sc = g["scores"]
        best = (min(sc) if g["game"].scoring == "low" else max(sc)) if sc else None
        rows.append({**g, "win_rate": g["wins"] / g["plays"], "best": best,
                     "avg": round(sum(sc) / len(sc), 1) if sc else None})
    return sorted(rows, key=lambda r: -r["plays"])


def head_to_head(player_id: int, plays: list[Play]) -> list[dict]:
    """For each opponent: games together, times you won, times they won (competitive only)."""
    opp: dict[int, dict] = {}
    for play in plays:
        if play.game.scoring == "coop":
            continue
        me = next((s for s in play.seats if s.player_id == player_id), None)
        if me is None:
            continue
        for s in play.seats:
            if s.player_id == player_id:
                continue
            o = opp.setdefault(s.player_id, {"player": s.player, "together": 0, "mine": 0, "theirs": 0})
            o["together"] += 1
            o["mine"] += int(me.is_winner and not s.is_winner)
            o["theirs"] += int(s.is_winner and not me.is_winner)
    return sorted(opp.values(), key=lambda o: (-o["together"], o["player"].display_name))


def most_played(plays: list[Play], limit: int = 6) -> list[tuple[Game, int]]:
    counts: dict[int, list] = {}
    for p in plays:
        counts.setdefault(p.game_id, [p.game, 0])[1] += 1
    return sorted(((g, n) for g, n in counts.values()), key=lambda t: -t[1])[:limit]


def category_stats(game: Game, plays: list[Play]) -> dict | None:
    """Per score-sheet line: each player's average, and the single best entry."""
    cats = {c.id: c for c in game.categories}
    if not cats:
        return None
    sums: dict[tuple[int, int], list[float]] = {}
    best: dict[int, tuple[float, int, int]] = {}  # cat -> (value, player_id, play_id)
    players: dict[int, Player] = {}
    for play in plays:
        seat_players = {s.player_id: s.player for s in play.seats}
        for cs in play.category_scores:
            if cs.category_id not in cats:
                continue
            players[cs.player_id] = seat_players.get(cs.player_id)
            sums.setdefault((cs.category_id, cs.player_id), []).append(cs.score)
            b = best.get(cs.category_id)
            better = b is None or (cs.score < b[0] if game.scoring == "low" else cs.score > b[0])
            if better:
                best[cs.category_id] = (cs.score, cs.player_id, play.id)
    if not sums:
        return None
    used = [c for c in game.categories if any(k[0] == c.id for k in sums)]
    plist = sorted((p for p in players.values() if p), key=lambda p: p.display_name)
    rows = []
    for c in used:
        avgs = {p.id: round(sum(v) / len(v), 1) for p in plist if (v := sums.get((c.id, p.id)))}
        b = best.get(c.id)
        rows.append({"cat": c, "avgs": avgs,
                     "best": (b[0], players.get(b[1]), b[2]) if b else None})
    return {"players": plist, "rows": rows}
