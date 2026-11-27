"""Streaks, rivalries, awards and achievements. All derived from history; nothing stored."""

from dataclasses import dataclass
from datetime import datetime

from . import nights as N
from .models import Play, Player
from .stats import head_to_head


def _competitive(plays: list[Play]) -> list[Play]:
    return [p for p in sorted(plays, key=lambda p: (p.played_at, p.id)) if p.game.scoring != "coop"]


def streaks(plays: list[Play]) -> dict[int, dict]:
    """Per player: current and longest run of consecutive competitive wins."""
    out: dict[int, dict] = {}
    for play in _competitive(plays):
        for s in play.seats:
            st = out.setdefault(s.player_id, {"player": s.player, "current": 0, "best": 0, "best_end": None})
            if s.is_winner:
                st["current"] += 1
                if st["current"] > st["best"]:
                    st["best"], st["best_end"] = st["current"], play.played_at
            else:
                st["current"] = 0
    return out


def rivals(player_id: int, plays: list[Play], min_games: int = 3) -> dict:
    h2h = [o for o in head_to_head(player_id, plays) if o["together"] >= min_games]
    nemesis = max((o for o in h2h if o["theirs"] > o["mine"]), key=lambda o: (o["theirs"] - o["mine"], o["theirs"]),
                  default=None)
    victim = max((o for o in h2h if o["mine"] > o["theirs"]), key=lambda o: (o["mine"] - o["theirs"], o["mine"]),
                 default=None)
    return {"nemesis": nemesis, "victim": victim}


def awards(plays: list[Play]) -> list[dict]:
    """Dashboard superlatives. Each: emoji, title, player, detail, link."""
    out: list[dict] = []
    st = streaks(plays)
    hot = max((s for s in st.values() if s["current"] >= 2 and s["player"].is_active),
              key=lambda s: s["current"], default=None)
    if hot:
        out.append({"emoji": "🔥", "title": "On a heater", "player": hot["player"],
                    "detail": f"{hot['current']} wins in a row"})
    best = max(st.values(), key=lambda s: s["best"], default=None)
    if best and best["best"] >= 3:
        out.append({"emoji": "📈", "title": "Longest win streak", "player": best["player"],
                    "detail": f"{best['best']} straight"})

    seconds: dict[int, list] = {}
    for p in _competitive(plays):
        if len(p.seats) < 3:
            continue
        for s in p.seats:
            if s.place == 2 and not s.is_winner:
                seconds.setdefault(s.player_id, [s.player, 0])[1] += 1
    bridesmaid = max(seconds.values(), key=lambda v: v[1], default=None)
    if bridesmaid and bridesmaid[1] >= 2:
        out.append({"emoji": "💐", "title": "Always the bridesmaid", "player": bridesmaid[0],
                    "detail": f"{bridesmaid[1]} second-place finishes"})

    longest = max((p for p in plays if p.duration_min), key=lambda p: p.duration_min, default=None)
    if longest:
        h, m = divmod(longest.duration_min, 60)
        out.append({"emoji": "⏳", "title": "Marathon", "player": None,
                    "detail": f"{longest.game.name}: {f'{h}h ' if h else ''}{m}m",
                    "link": f"/plays/{longest.id}"})

    counts: dict[int, list] = {}
    for p in plays:
        for s in p.seats:
            counts.setdefault(s.player_id, [s.player, 0])[1] += 1
    regular = max((v for v in counts.values() if v[0].is_active), key=lambda v: v[1], default=None)
    if regular and regular[1] >= 5:
        out.append({"emoji": "🪑", "title": "Never misses a game", "player": regular[0],
                    "detail": f"{regular[1]} plays"})
    return out


@dataclass
class Achievement:
    emoji: str
    title: str
    detail: str
    earned: datetime | None = None
    count: int = 0  # for repeatable ones (e.g. night champion x3)


def achievements(player: Player, plays: list[Play]) -> list[Achievement]:
    """`plays` should be every play this player was in, any order."""
    pid = player.id
    mine = sorted((p for p in plays if any(s.player_id == pid for s in p.seats)), key=lambda p: (p.played_at, p.id))
    got: dict[str, Achievement] = {}

    def award(key, emoji, title, detail, when):
        if key not in got:
            got[key] = Achievement(emoji, title, detail, when)

    wins = streak = coop_wins = 0
    games_seen: set[int] = set()
    per_game_wins: dict[int, int] = {}
    for i, p in enumerate(mine, 1):
        seat = next(s for s in p.seats if s.player_id == pid)
        games_seen.add(p.game_id)
        if i == 1:
            award("first", "🎲", "Welcome to the table", "Logged a first play", p.played_at)
        if i == 25:
            award("p25", "🪑", "Regular", "25 plays", p.played_at)
        if i == 100:
            award("p100", "💯", "Centurion", "100 plays", p.played_at)
        if len(games_seen) == 10:
            award("explorer", "🧭", "Explorer", "Played 10 different games", p.played_at)
        if p.game.scoring == "coop":
            if seat.is_winner:
                coop_wins += 1
                if coop_wins == 5:
                    award("team", "🤝", "Team player", "5 co-op victories", p.played_at)
            continue
        if seat.is_winner:
            wins += 1
            streak += 1
            per_game_wins[p.game_id] = per_game_wins.get(p.game_id, 0) + 1
            if wins == 1:
                award("firstwin", "🥇", "First blood", "First win", p.played_at)
            if wins == 10:
                award("w10", "🏅", "Ten-time winner", "10 wins", p.played_at)
            if wins == 50:
                award("w50", "🏆", "Trophy case", "50 wins", p.played_at)
            if streak == 3:
                award("hat", "🎩", "Hat trick", "3 wins in a row", p.played_at)
            if streak == 5:
                award("fire", "🔥", "Unstoppable", "5 wins in a row", p.played_at)
            if per_game_wins[p.game_id] == 10:
                award(f"spec{p.game_id}", "🎯", f"{p.game.name} specialist", f"10 wins at {p.game.name}", p.played_at)
        else:
            streak = 0
        if seat.place is not None and len(p.seats) >= 3 and seat.place == len(p.seats) and \
                not seat.is_winner:
            award("last", "🐢", "Participation trophy", "Finished last in a 3+ player game", p.played_at)

    champ_nights = 0
    for night in N.group(mine):
        if any(c.id == pid for c in night.champions):
            champ_nights += 1
            if champ_nights == 1:
                award("champ", "👑", "Champion of the night", "Most wins on a game night", night.plays[-1].played_at)
        sweeper = night.swept_by()
        if sweeper and sweeper.id == pid:
            award("sweep", "🧹", "Clean sweep", "Won every game of a night (3+ games)", night.plays[-1].played_at)
    if "champ" in got:
        got["champ"].count = champ_nights

    return sorted(got.values(), key=lambda a: a.earned or datetime.min, reverse=True)
