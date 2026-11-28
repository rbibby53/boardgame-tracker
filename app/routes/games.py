from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import asyncio

from .. import bgg, media, ratings, stats
from ..auth import current_user, require, verify_csrf
from ..db import get_db
from ..models import SCORING_TYPES, CategoryScore, Game, GameCategory, Play, Player, natural_key
from ..templating import flash, render

router = APIRouter(prefix="/games")
api = APIRouter(prefix="/api/bgg")


@api.get("/search")
async def bgg_search(q: str = "", user: Player = Depends(require("manage_games"))):
    try:
        return {"results": await asyncio.to_thread(bgg.search, q[:100])}
    except bgg.BggError as e:
        return JSONResponse({"error": str(e)}, 502)


@api.get("/thing/{bgg_id}")
async def bgg_thing(bgg_id: int, user: Player = Depends(require("manage_games"))):
    try:
        return await asyncio.to_thread(bgg.thing, bgg_id)
    except bgg.BggError as e:
        return JSONResponse({"error": str(e)}, 502)


def _cover_from_bgg(game: Game) -> str | None:
    """Download the BGG cover for game.bgg_id. Returns an error message or None."""
    try:
        info = bgg.thing(game.bgg_id)
        if not info.get("image"):
            return "BoardGameGeek has no image for this game."
        media.save_cover(game.id, bgg.fetch_image(info["image"]))
    except (bgg.BggError, media.ImageError) as e:
        return str(e)
    game.has_cover = True
    return None


async def _maybe_bgg_cover(request: Request, db: Session, game: Game) -> None:
    form = await request.form()
    if form.get("bgg_cover") == "1" and game.bgg_id and bgg.enabled():
        err = await asyncio.to_thread(_cover_from_bgg, game)
        db.commit()
        if err:
            flash(request, f"Saved, but the cover didn't download: {err}", "err")


def _int_or_none(v) -> int | None:
    v = (v or "").strip() if isinstance(v, str) else v
    return int(v) if v not in (None, "") else None


def _sync_categories(db: Session, game: Game, raw: str) -> str | None:
    names: list[str] = []
    for line in raw.splitlines():
        n = line.strip()[:80]
        if n and n.lower() not in {x.lower() for x in names}:
            names.append(n)
    if len(names) > 30:
        return "That's a lot of score sheet lines. Keep it to 30 or fewer."
    existing = {c.name.lower(): c for c in game.categories}
    keep = set()
    for pos, name in enumerate(names):
        cat = existing.get(name.lower())
        if cat is None:
            cat = GameCategory(name=name)
            game.categories.append(cat)
        cat.name, cat.position, cat.archived = name, pos, False
        keep.add(name.lower())
    for key, cat in existing.items():
        if key in keep:
            continue
        used = cat.id is not None and db.scalar(
            select(func.count()).select_from(CategoryScore).where(CategoryScore.category_id == cat.id))
        if used:
            cat.archived = True
            cat.position = 1000 + cat.position
        else:
            game.categories.remove(cat)
    return None


async def _read_game_form(request: Request, game: Game, db: Session) -> str | None:
    form = await request.form()
    name = (form.get("name") or "").strip()[:200]
    if not name:
        return "Name is required."
    scoring = form.get("scoring") or "high"
    if scoring not in SCORING_TYPES:
        return "Unknown scoring type."
    try:
        mn, mx = _int_or_none(form.get("min_players")), _int_or_none(form.get("max_players"))
        bgg = _int_or_none(form.get("bgg_id"))
    except ValueError:
        return "Player counts and BGG ID must be numbers."
    if mn and mx and mn > mx:
        return "Min players is greater than max players."
    game.name = name
    game.scoring = scoring
    game.uses_rounds = form.get("uses_rounds") == "on"
    game.owned = form.get("owned") == "on"
    game.round_label = (form.get("round_label") or "").strip()[:40] or None
    try:
        pt = _int_or_none(form.get("play_time_min"))
        yr = _int_or_none(form.get("year"))
    except ValueError:
        return "Play time and year must be numbers."
    game.min_players, game.max_players, game.bgg_id = mn, mx, bgg
    game.play_time_min, game.year = pt, yr
    game.notes = (form.get("notes") or "").strip() or None
    err = _read_parent(db, game, form.get("parent_id"))
    return err or _sync_categories(db, game, form.get("categories") or "")


def _read_parent(db: Session, game: Game, raw) -> str | None:
    """'Part of' a collection. One level only, so a collection can't join another one."""
    try:
        pid = _int_or_none(raw)
    except ValueError:
        return "Unknown collection."
    if pid is None:
        game.parent_id = None
        return None
    parent = db.get(Game, pid)
    if parent is None:
        return "Unknown collection."
    if game.id is not None and parent.id == game.id:
        return "A game can't be part of itself."
    if parent.parent_id is not None:
        return f"{parent.name} is itself part of {parent.parent.name}. Pick the collection instead."
    if game.id is not None and game.children:
        return f"{game.name} has its own mini-games, so it can't be part of another collection."
    # parent_id, not the relationship: assigning game.parent would cascade a new game into the session
    # before the duplicate-name check, and that query's autoflush would then hit the UNIQUE constraint.
    game.parent_id = parent.id
    game.owned = parent.owned  # the shelf is the collection's
    return None


def _collections(db: Session, exclude: Game | None = None) -> tuple[list[Game], list[Game]]:
    """(existing collections, other top-level games) for the 'Part of' picker."""
    top = db.scalars(select(Game).where(Game.parent_id.is_(None), Game.archived.is_(False))
                     .options(selectinload(Game.children)).order_by(Game.name)).all()
    top = [g for g in top if exclude is None or g.id != exclude.id]
    top.sort(key=lambda g: natural_key(g.name))
    return [g for g in top if g.children], [g for g in top if not g.children]


def _form_ctx(db: Session, game: Game | None, **extra) -> dict:
    cols, others = _collections(db, exclude=game)
    return {"bgg_enabled": bgg.enabled(), "game": game, "collections": cols, "other_games": others, **extra}


@router.get("")
def list_games(request: Request, archived: bool = False, shelf: bool = False, user: Player = Depends(current_user),
               db: Session = Depends(get_db)):
    q = (select(Game, func.count(Play.id), func.max(Play.played_at)).outerjoin(Play)
         .where(Game.archived.is_(archived)).group_by(Game.id).order_by(Game.name))
    if shelf:
        q = q.where(Game.owned.is_(True))
    rows = db.execute(q).all()
    # Mini-games are listed under their collection; one whose collection isn't in this view stands alone.
    shown = {g.id for g, _, _ in rows}
    kids: dict[int, list] = {}
    for g, n, last in rows:
        if g.parent_id in shown:
            kids.setdefault(g.parent_id, []).append((g, n, last))
    entries = []
    for g, n, last in rows:
        if g.parent_id in shown:
            continue
        ch = kids.get(g.id, [])
        lasts = [x for x in (last, *(c[2] for c in ch)) if x]
        entries.append({"game": g, "plays": n + sum(c[1] for c in ch), "last": max(lasts) if lasts else None,
                        "children": sorted(ch, key=lambda c: natural_key(c[0].name))})
    entries.sort(key=lambda e: natural_key(e["game"].name))
    any_owned = bool(db.scalar(select(func.count(Game.id)).where(Game.owned.is_(True))))
    return render(request, "games/list.html", {"entries": entries, "archived": archived, "shelf": shelf,
                                               "any_owned": any_owned})


@router.get("/new")
def new_game(request: Request, parent: int | None = None, user: Player = Depends(require("manage_games")),
             db: Session = Depends(get_db)):
    return render(request, "games/form.html", _form_ctx(db, None, prefill_parent=parent))


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_game(request: Request, user: Player = Depends(require("manage_games")), db: Session = Depends(get_db)):
    game = Game()
    err = await _read_game_form(request, game, db)
    if not err and db.scalar(select(Game).where(func.lower(Game.name) == game.name.lower())):
        err = f"'{game.name}' already exists."
    if err:
        return render(request, "games/form.html", _form_ctx(db, None, error=err, form=await request.form()), 422)
    db.add(game)
    db.commit()
    flash(request, f"Added {game.name}.")
    await _maybe_bgg_cover(request, db, game)
    nxt = (await request.form()).get("next")
    if nxt == "log":
        return RedirectResponse(f"/plays/new?game={game.id}", status_code=303)
    return RedirectResponse(f"/games/{game.id}", status_code=303)


def _player_span(games: list[Game]) -> tuple[int | None, int | None]:
    mins = [g.min_players for g in games if g.min_players]
    maxs = [g.max_players for g in games if g.max_players]
    return (min(mins) if mins else None, max(maxs) if maxs else None)


@router.get("/{game_id}")
def show_game(game_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404)
    kids = sorted(game.children, key=lambda c: natural_key(c.name))
    rt = ratings.table(db)
    if kids:
        # A collection: plays and standings across its mini-games. Scores, records, score sheets and ratings
        # belong to each mini-game (they score differently), so those stay on the mini-game pages.
        plays = stats.load_plays(db, game_ids=[game.id, *(c.id for c in kids)])
        per_game = {}
        for p in plays:
            per_game[p.game_id] = per_game.get(p.game_id, 0) + 1
        records = stats.records(game, plays)
        records.update(best=None, margin=None)
        return render(request, "games/show.html", {
            "game": game, "rt": rt, "kids": kids, "kid_plays": per_game, "span": _player_span(kids),
            "rating_series": None, "board": stats.leaderboard(plays, include_coop=True), "records": records,
            "recent": list(reversed(plays[-10:])), "play_count": len(plays), "score_series": None, "sheet": None,
            "wins_series": stats.cumulative_wins_series(plays),
        })
    plays = stats.load_plays(db, game_id=game_id)
    return render(request, "games/show.html", {
        "game": game,
        "rt": rt,
        "kids": [],
        "rating_series": ratings.series(rt, plays, game_id=game_id),
        "board": stats.leaderboard(plays, include_coop=True),
        "records": stats.records(game, plays),
        "recent": list(reversed(plays[-10:])),
        "play_count": len(plays),
        "score_series": stats.score_series(plays),
        "sheet": stats.category_stats(game, plays),
        "wins_series": stats.cumulative_wins_series(plays),
    })


@router.get("/{game_id}/edit")
def edit_game(game_id: int, request: Request, user: Player = Depends(require("manage_games")), db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404)
    return render(request, "games/form.html", _form_ctx(db, game))


@router.post("/{game_id}", dependencies=[Depends(verify_csrf)])
async def update_game(game_id: int, request: Request, user: Player = Depends(require("manage_games")),
                      db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404)
    err = await _read_game_form(request, game, db)
    if not err:
        dupe = db.scalar(select(Game).where(func.lower(Game.name) == game.name.lower(), Game.id != game.id))
        if dupe:
            err = f"'{game.name}' already exists."
    if err:
        db.rollback()
        return render(request, "games/form.html", _form_ctx(db, db.get(Game, game_id), error=err,
                                                            form=await request.form()), 422)
    game.cascade_to_children()
    db.commit()
    flash(request, "Game updated. Existing plays keep the winners they were saved with.")
    await _maybe_bgg_cover(request, db, game)
    return RedirectResponse(f"/games/{game.id}", status_code=303)


@router.post("/{game_id}/archive", dependencies=[Depends(verify_csrf)])
def archive_game(game_id: int, request: Request, user: Player = Depends(require("archive_games")),
                 db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404)
    game.archived = not game.archived
    game.cascade_to_children(archived=True)
    db.commit()
    extra = f" with its {len(game.children)} mini-games" if game.children else ""
    flash(request, f"{game.name} {'archived' if game.archived else 'restored'}{extra}.")
    return RedirectResponse(f"/games/{game.id}", status_code=303)


@router.post("/{game_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_game(game_id: int, request: Request, user: Player = Depends(require("archive_games")),
                db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404)
    if db.scalar(select(func.count()).select_from(Play).where(Play.game_id == game_id)):
        flash(request, "This game has plays logged. Archive it instead, or delete its plays first.", "err")
        return RedirectResponse(f"/games/{game.id}", status_code=303)
    freed = len(game.children)
    for c in list(game.children):  # mini-games survive as standalone games
        c.parent_id = None
    db.delete(game)
    db.commit()
    media.delete_cover(game_id)
    flash(request, "Game deleted." + (f" Its {freed} mini-games are standalone games now." if freed else ""))
    return RedirectResponse("/games", status_code=303)
