from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


PALETTE = [
    "#2f7ed8", "#e4572e", "#29a36a", "#a65fd6", "#f2a541",
    "#17a2b8", "#d63384", "#7d8b2b", "#8c564b", "#5f6caf",
]

SCORING_TYPES = {
    "high": "Highest score wins",
    "low": "Lowest score wins",
    "winloss": "Win / loss (no points)",
    "coop": "Co-op (team vs. game)",
}


class Player(Base):
    """A person who plays. Either synced from Authentik (source='sso') or a local guest."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(150), index=True)
    display_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(9))
    source: Mapped[str] = mapped_column(String(10), default="local")  # sso | local
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def display_color(self) -> str:
        return self.color or PALETTE[(self.id or 0) % len(PALETTE)]

    @property
    def initials(self) -> str:
        parts = [p for p in self.display_name.split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    scoring: Mapped[str] = mapped_column(String(10), default="high")
    uses_rounds: Mapped[bool] = mapped_column(Boolean, default=False)
    round_label: Mapped[str | None] = mapped_column(String(40))  # "Round", "Hand", "Era"...
    min_players: Mapped[int | None] = mapped_column(Integer)
    max_players: Mapped[int | None] = mapped_column(Integer)
    bgg_id: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    plays: Mapped[list["Play"]] = relationship(back_populates="game")

    @property
    def scoring_label(self) -> str:
        return SCORING_TYPES.get(self.scoring, self.scoring)


class Play(Base):
    """One session of a game."""

    __tablename__ = "plays"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="RESTRICT"), index=True)
    played_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    location: Mapped[str | None] = mapped_column(String(200))
    duration_min: Mapped[int | None] = mapped_column(Integer)
    expansions: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    coop_won: Mapped[bool | None] = mapped_column(Boolean)
    winners_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("players.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    game: Mapped[Game] = relationship(back_populates="plays")
    created_by: Mapped[Player | None] = relationship()
    seats: Mapped[list["PlaySeat"]] = relationship(
        back_populates="play", cascade="all, delete-orphan", order_by="PlaySeat.id"
    )
    rounds: Mapped[list["Round"]] = relationship(
        back_populates="play", cascade="all, delete-orphan", order_by="Round.number"
    )

    @property
    def winners(self) -> list[Player]:
        return [s.player for s in self.seats if s.is_winner]

    @property
    def ordered_seats(self) -> list["PlaySeat"]:
        return sorted(self.seats, key=lambda s: (not s.is_winner, s.place is None, s.place or 0, s.id or 0))


class PlaySeat(Base):
    """A player's result in one play."""

    __tablename__ = "play_seats"
    __table_args__ = (UniqueConstraint("play_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    play_id: Mapped[int] = mapped_column(ForeignKey("plays.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), index=True)
    score: Mapped[float | None] = mapped_column(Float)
    score_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    place: Mapped[int | None] = mapped_column(Integer)
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False)
    team: Mapped[str | None] = mapped_column(String(60))

    play: Mapped[Play] = relationship(back_populates="seats")
    player: Mapped[Player] = relationship()


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("play_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    play_id: Mapped[int] = mapped_column(ForeignKey("plays.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(100))

    play: Mapped[Play] = relationship(back_populates="rounds")
    scores: Mapped[list["RoundScore"]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )


class RoundScore(Base):
    __tablename__ = "round_scores"
    __table_args__ = (UniqueConstraint("round_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"))
    score: Mapped[float | None] = mapped_column(Float)
    won: Mapped[bool] = mapped_column(Boolean, default=False)

    round: Mapped[Round] = relationship(back_populates="scores")
    player: Mapped[Player] = relationship()


class SyncLog(Base):
    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ok: Mapped[bool] = mapped_column(Boolean)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    deactivated: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
