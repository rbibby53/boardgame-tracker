"""Fill an empty database with fictional demo data (for screenshots and trying the app).

    DEV_MODE=true SECRET_KEY=... DATABASE_URL=sqlite:///./demo.db DATA_DIR=./demo-data python -m app.demo

Refuses to run if any plays already exist. All people are made up.
"""

import random
import sys
from datetime import date, datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

from . import media
from .db import SessionLocal
from .main import run_migrations
from .models import (CategoryScore, Game, GameCategory, GameNight, Play, PlaySeat, Player, Poll, PollOption,
                     PollVote, Round, RoundScore, utcnow)
from .scoring import PlayPayload, compute

PEOPLE = [  # (name, username, skill 0-1)
    ("Alex Rivera", "alex", 0.72), ("Jordan Lee", "jordan", 0.58), ("Sam Patel", "sam", 0.66),
    ("Casey Morgan", "casey", 0.45), ("Riley Chen", "riley", 0.61), ("Taylor Brooks", "taylor", 0.50),
]
GUEST = ("Grandpa Joe", 0.55)

GAMES = [  # name, scoring, min, max, minutes, year, color, extra
    ("Catan", "high", 3, 4, 90, 1995, "#d9822b", {}),
    ("Azul", "high", 2, 4, 45, 2017, "#2f7ed8", {}),
    ("Wingspan", "high", 1, 5, 70, 2019, "#29a36a",
     {"cats": ["Birds", "Bonus cards", "Round goals", "Eggs", "Food on cards", "Tucked cards"]}),
    ("7 Wonders", "high", 3, 7, 30, 2010, "#a65fd6",
     {"cats": ["Military", "Coins", "Wonders", "Civilian", "Science", "Commerce", "Guilds"]}),
    ("Ticket to Ride", "high", 2, 5, 60, 2004, "#c0392b", {}),
    ("Hearts", "low", 4, 4, 40, None, "#d63384", {"rounds": "Hand"}),
    ("Pandemic", "coop", 2, 4, 45, 2008, "#17a2b8", {}),
    ("Hive", "winloss", 2, 2, 20, 2001, "#7d8b2b", {}),
    ("Codenames", "winloss", 4, 8, 15, 2015, "#5f6caf", {"teams": True}),
]


def _cover(game_id: int, name: str, color: str) -> None:
    im = Image.new("RGB", (400, 400), color)
    d = ImageDraw.Draw(im)
    for i in range(0, 400, 40):  # subtle diagonal texture
        d.line([(i, 0), (0, i)], fill=_shade(color, 0.92), width=12)
        d.line([(400, i), (i, 400)], fill=_shade(color, 0.92), width=12)
    words = name.split()
    mono = (words[0][0] + (words[1][0] if len(words) > 1 else words[0][1])).upper()
    try:
        font = ImageFont.load_default(size=150)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), mono, font=font)
    d.text(((400 - (box[2] - box[0])) / 2 - box[0], (400 - (box[3] - box[1])) / 2 - box[1]), mono,
           fill="white", font=font)
    import io

    buf = io.BytesIO()
    im.save(buf, "PNG")
    media.save_cover(game_id, buf.getvalue())


def _shade(hex_color: str, f: float) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(int(h[i:i + 2], 16) * f) for i in (0, 2, 4))


def seed() -> None:
    run_migrations()
    rng = random.Random(1995)
    with SessionLocal() as db:
        if db.query(Play).first():
            sys.exit("Refusing to seed: this database already has plays.")

        people = []
        for i, (name, user, skill) in enumerate(PEOPLE):
            p = Player(display_name=name, username=user, source="sso", sub=f"demo-{user}",
                       is_admin=(i == 0), last_login=utcnow())
            p.skill = skill
            people.append(p)
        guest = Player(display_name=GUEST[0], source="local")
        guest.skill = GUEST[1]
        db.add_all(people + [guest])

        games = []
        for name, scoring, mn, mx, minutes, year, color, extra in GAMES:
            g = Game(name=name, scoring=scoring, min_players=mn, max_players=mx, play_time_min=minutes, year=year,
                     uses_rounds=bool(extra.get("rounds")), round_label=extra.get("rounds"), owned=True)
            for pos, c in enumerate(extra.get("cats", [])):
                g.categories.append(GameCategory(name=c, position=pos))
            g.color, g.extra = color, extra
            games.append(g)
        db.add_all(games)
        db.flush()
        for g in games:
            _cover(g.id, g.name, g.color)
            g.has_cover = True

        start = date.today() - timedelta(days=240)
        day, nights = start, []
        while day < date.today() - timedelta(days=1):
            if day.weekday() in (4, 5) and rng.random() < 0.55:  # Fri/Sat game nights
                nights.append(day)
            day += timedelta(days=1)
        nights.append(date.today() - timedelta(days=1))

        for night in nights:
            crowd = rng.sample(people, rng.randint(3, 5)) + ([guest] if rng.random() < 0.2 else [])
            when = datetime.combine(night, datetime.min.time()) + timedelta(hours=19, minutes=rng.choice([0, 15, 30]))
            for _ in range(rng.randint(2, 4)):
                g = rng.choice([x for x in games if (x.min_players or 1) <= len(crowd) or x.scoring == "winloss"])
                table = _table(rng, g, crowd)
                if not table:
                    continue
                play = _play(rng, db, g, table, when)
                when += timedelta(minutes=(play.duration_min or 30) + rng.randint(5, 20))
        db.flush()

        titles = {nights[-1]: "Friday rematch night", nights[len(nights) // 2]: "Cousins in town"}
        for d, t in titles.items():
            db.add(GameNight(night_date=d, title=t, notes="Pizza from the usual place. Someone owes a rematch."))

        # A closed vote and an open one
        for closed, title in ((True, "Saturday: what's first?"), (False, "What should we play tonight?")):
            poll = Poll(title=title, created_by_id=people[0].id)
            picks = rng.sample([g for g in games if g.max_players and g.max_players >= 4], 5)
            for g in picks:
                opt = PollOption(game_id=g.id)
                for p in people:
                    if rng.random() < 0.45:
                        opt.votes.append(PollVote(player_id=p.id))
                poll.options.append(opt)
            if closed:
                poll.closed_at = utcnow() - timedelta(days=3)
                poll.winner_game_id = max(poll.options, key=lambda o: len(o.votes)).game_id
            db.add(poll)
        db.commit()
        print(f"Seeded {len(people) + 1} players, {len(games)} games, {db.query(Play).count()} plays "
              f"over {len(nights)} nights.")


def _table(rng, g, crowd):
    if g.name == "Hive":
        return rng.sample(crowd, 2)
    if g.extra.get("teams") and len(crowd) < 4:
        return None
    n = min(len(crowd), g.max_players or len(crowd))
    return crowd[:n] if n >= (g.min_players or 1) else None


def _play(rng, db, g, table, when) -> Play:
    perf = {p.id: p.skill + rng.gauss(0, 0.18) for p in table}
    seats, rounds, cats = [], [], {}
    coop_won = None
    if g.scoring == "coop":
        coop_won = rng.random() < 0.55
        seats = [{"player_id": p.id} for p in table]
    elif g.scoring == "winloss":
        if g.extra.get("teams"):
            half = len(table) // 2
            order = rng.sample(table, len(table))
            red, blue = order[:half], order[half:]
            red_wins = sum(perf[p.id] for p in red) / len(red) > sum(perf[p.id] for p in blue) / len(blue)
            seats = [{"player_id": p.id, "team": "Red", "winner": red_wins} for p in red] + \
                    [{"player_id": p.id, "team": "Blue", "winner": not red_wins} for p in blue]
        else:
            best = max(table, key=lambda p: perf[p.id])
            seats = [{"player_id": p.id, "winner": p is best} for p in table]
    elif g.extra.get("cats"):
        seats = [{"player_id": p.id} for p in table]
        for c in g.categories:
            cats[c.id] = {p.id: max(0, round(rng.gauss(6 + 9 * perf[p.id], 3))) for p in table}
    elif g.extra.get("rounds"):  # Hearts: low wins
        seats = [{"player_id": p.id} for p in table]
        for _ in range(rng.randint(5, 8)):
            pts = {p.id: max(0, round(rng.gauss(10 - 9 * perf[p.id], 4))) for p in table}
            if rng.random() < 0.12:  # someone shoots the moon
                shooter = rng.choice(table)
                pts = {p.id: (0 if p is shooter else 26) for p in table}
            low = min(pts.values())
            rounds.append({"scores": pts, "won": [pid for pid, v in pts.items() if v == low]})
    else:
        base = {"Catan": (6, 5), "Azul": (40, 45), "Ticket to Ride": (70, 60)}.get(g.name, (20, 30))
        seats = [{"player_id": p.id, "score": max(0, round(base[0] + base[1] * perf[p.id] + rng.gauss(0, 4)))}
                 for p in table]
        if g.name == "Catan":
            for s in seats:
                s["score"] = min(10, max(3, round(4 + 7 * perf[s["player_id"]])))
            top = max(seats, key=lambda s: s["score"])
            top["score"] = 10

    payload = PlayPayload(players=seats, rounds=rounds, categories=cats,
                          winners_manual=g.scoring == "winloss", coop_won=coop_won)
    play = Play(game_id=g.id, played_at=when, location=rng.choice(["Alex's place", "Sam's", "The game café", None]),
                duration_min=max(10, round((g.play_time_min or 30) * rng.uniform(0.8, 1.3))),
                coop_won=coop_won if g.scoring == "coop" else None, winners_manual=payload.winners_manual,
                created_by_id=table[0].id)
    for r in compute(g.scoring, payload):
        play.seats.append(PlaySeat(player_id=r.player_id, score=r.score, score_manual=False, place=r.place,
                                   is_winner=r.is_winner, team=r.team))
    for i, rin in enumerate(payload.rounds, 1):
        rnd = Round(number=i)
        for pid, v in rin.scores.items():
            rnd.scores.append(RoundScore(player_id=pid, score=v, won=pid in rin.won))
        play.rounds.append(rnd)
    for cid, row in payload.categories.items():
        for pid, v in row.items():
            play.category_scores.append(CategoryScore(category_id=cid, player_id=pid, score=v))
    db.add(play)
    return play


if __name__ == "__main__":
    seed()
