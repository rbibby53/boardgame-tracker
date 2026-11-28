"""Game nights: plays grouped by evening. A play before NIGHT_CUTOFF_HOUR counts toward the previous day.
Plays logged during a live game night (app/live.py) belong to that night instead, so two nights on the same
evening (one at Ryan's, one at his parents') stay separate, each with its own champion."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .config import get_settings
from .models import Play, Player


def night_of(dt: datetime) -> date:
    return (dt - timedelta(hours=get_settings().night_cutoff_hour)).date()


def bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, datetime.min.time()) + timedelta(hours=get_settings().night_cutoff_hour)
    return start, start + timedelta(days=1)


@dataclass
class NightRow:
    player: Player
    plays: int = 0
    wins: int = 0  # competitive wins only


@dataclass
class Night:
    day: date
    plays: list[Play] = field(default_factory=list)
    live: object | None = None  # the LiveNight these plays were logged at, if any

    @property
    def url(self) -> str:
        return f"/live/{self.live.id}" if self.live else f"/nights/{self.day.isoformat()}"

    @property
    def rows(self) -> list[NightRow]:
        rows: dict[int, NightRow] = {}
        for p in self.plays:
            for s in p.seats:
                r = rows.setdefault(s.player_id, NightRow(s.player))
                r.plays += 1
                if s.is_winner and p.game.scoring != "coop":
                    r.wins += 1
        return sorted(rows.values(), key=lambda r: (-r.wins, -r.plays, r.player.display_name))

    @property
    def champions(self) -> list[Player]:
        rows = self.rows
        top = rows[0].wins if rows else 0
        return [r.player for r in rows if top > 0 and r.wins == top]

    @property
    def minutes(self) -> int:
        return sum(p.duration_min or 0 for p in self.plays)

    @property
    def coop(self) -> tuple[int, int]:
        co = [p for p in self.plays if p.game.scoring == "coop"]
        return sum(1 for p in co if p.coop_won), len(co)

    def swept_by(self) -> Player | None:
        """Someone who won every competitive game of a night with 3+ such games."""
        comp = [p for p in self.plays if p.game.scoring != "coop"]
        if len(comp) < 3:
            return None
        for r in self.rows:
            if r.wins == len(comp):
                return r.player
        return None


def group(plays: list[Play]) -> list[Night]:
    """Newest night first; plays within a night in play order. A live night's plays form their own night."""
    nights: dict[tuple, Night] = {}
    for p in sorted(plays, key=lambda p: (p.played_at, p.id)):
        if p.live_night_id:
            key = ("live", p.live_night_id)
            if key not in nights:
                nights[key] = Night(night_of(p.played_at), live=p.live_night)
        else:
            key = ("day", night_of(p.played_at))
            nights.setdefault(key, Night(key[1]))
        nights[key].plays.append(p)
    return sorted(nights.values(), key=lambda n: (n.day, n.plays[0].played_at), reverse=True)
