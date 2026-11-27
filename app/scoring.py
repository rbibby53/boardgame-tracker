"""Pure scoring logic: turns submitted seats + rounds into totals, places and winners."""

from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator, model_validator


class SeatIn(BaseModel):
    player_id: int
    score: float | None = None  # manual final score; None = derive from rounds
    winner: bool = False
    team: str | None = None

    @field_validator("team")
    @classmethod
    def blank_team(cls, v):
        v = (v or "").strip()
        return v[:60] or None


class RoundIn(BaseModel):
    label: str | None = None
    scores: dict[int, float | None] = Field(default_factory=dict)
    won: list[int] = Field(default_factory=list)

    @field_validator("label")
    @classmethod
    def blank_label(cls, v):
        v = (v or "").strip()
        return v[:100] or None


class PlayPayload(BaseModel):
    players: list[SeatIn]
    rounds: list[RoundIn] = Field(default_factory=list)
    # score sheet: {category_id: {player_id: points}}
    categories: dict[int, dict[int, float | None]] = Field(default_factory=dict)
    winners_manual: bool = False
    coop_won: bool | None = None

    @model_validator(mode="after")
    def check(self):
        if not self.players:
            raise ValueError("Pick at least one player.")
        ids = [s.player_id for s in self.players]
        if len(ids) != len(set(ids)):
            raise ValueError("A player is listed twice.")
        seated = set(ids)
        for i, r in enumerate(self.rounds, 1):
            extra = (set(r.scores) | set(r.won)) - seated
            if extra:
                raise ValueError(f"Round {i} has a score for someone who isn't playing.")
        for cat in self.categories.values():
            if set(cat) - seated:
                raise ValueError("The score sheet has a score for someone who isn't playing.")
        return self


@dataclass
class SeatResult:
    player_id: int
    score: float | None
    score_manual: bool
    place: int | None
    is_winner: bool
    team: str | None
    rounds_won: int


def _rank(values: dict[int, float], reverse: bool) -> dict[int, int]:
    """Standard competition ranking (1, 1, 3)."""
    ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=reverse)
    places: dict[int, int] = {}
    prev = None
    for idx, (pid, val) in enumerate(ordered, 1):
        if prev is None or val != prev[1]:
            prev = (idx, val)
        places[pid] = prev[0]
    return places


def compute(scoring: str, payload: PlayPayload) -> list[SeatResult]:
    seat_ids = [s.player_id for s in payload.players]

    round_totals: dict[int, float] = {}
    round_wins: dict[int, int] = {pid: 0 for pid in seat_ids}
    for r in payload.rounds:
        for pid, val in r.scores.items():
            if val is not None:
                round_totals[pid] = round_totals.get(pid, 0.0) + val
        for pid in set(r.won):
            round_wins[pid] += 1

    sheet_totals: dict[int, float] = {}
    for cat in payload.categories.values():
        for pid, val in cat.items():
            if val is not None:
                sheet_totals[pid] = sheet_totals.get(pid, 0.0) + val

    totals: dict[int, float | None] = {}
    manual: dict[int, bool] = {}
    for s in payload.players:
        if s.score is not None:
            totals[s.player_id], manual[s.player_id] = s.score, True
        elif s.player_id in sheet_totals:
            totals[s.player_id], manual[s.player_id] = sheet_totals[s.player_id], False
        else:
            totals[s.player_id], manual[s.player_id] = round_totals.get(s.player_id), False

    places: dict[int, int] = {}
    if scoring in ("high", "low"):
        scored = {pid: v for pid, v in totals.items() if v is not None}
        places = _rank(scored, reverse=(scoring == "high"))
    elif scoring == "winloss" and payload.rounds:
        places = _rank({pid: float(w) for pid, w in round_wins.items()}, reverse=True)

    winners: set[int]
    if scoring == "coop":
        winners = set(seat_ids) if payload.coop_won else set()
    elif payload.winners_manual:
        winners = {s.player_id for s in payload.players if s.winner}
    elif scoring in ("high", "low"):
        winners = {pid for pid, p in places.items() if p == 1}
    elif scoring == "winloss":
        best = max(round_wins.values(), default=0)
        winners = {pid for pid, w in round_wins.items() if best > 0 and w == best}
    else:
        winners = set()

    return [
        SeatResult(
            player_id=s.player_id,
            score=totals[s.player_id],
            score_manual=manual[s.player_id],
            place=places.get(s.player_id),
            is_winner=s.player_id in winners,
            team=s.team,
            rounds_won=round_wins[s.player_id],
        )
        for s in payload.players
    ]


def fmt_score(v: float | None) -> str:
    if v is None:
        return "—"
    return str(int(v)) if float(v).is_integer() else f"{v:g}"
