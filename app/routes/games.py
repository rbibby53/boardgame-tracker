from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import asyncio

from .. import bgg, media, ratings, stats
from ..auth import current_user, require, verify_csrf
from ..db import get_db
from ..models import SCORING_TYPES, CategoryScore, Game, GameCategory, Play, Player
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
    return _sync_categories(db, game, form.get("categories") or "")


@router.get("")
def list_games(request: Request, archived: bool = False, shelf: bool = False, user: Player = Depends(current_user),
               db: Session = Depends(get_db)):
    q = (select(Game, func.count(Play.id), func.max(Play.played_at)).outerjoin(Play)
         .where(Game.archived.is_(archived)).group_by(Game.id).order_by(Game.name))
    if shelf:
        q = q.where(Game.owned.is_(True))
    rows = db.execute(q).all()
    any_owned = bool(db.scalar(select(func.count(Game.id)).where(Game.owned.is_(True))))
    return render(request, "games/list.html", {"rows": rows, "archived": archived, "shelf": shelf,
                                               "any_owned": any_owned})


@router.get("/new")
def new_game(request: Request, user: Player = Depends(require("manage_games"))):
    return render(request, "games/form.html", {"bgg_enabled": bgg.enabled(), "game": None})


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_game(request: Request, user: Player = Depends(require("manage_games")), db: Session = Depends(get_db)):
    game = Game()
    err = await _read_game_form(request, game, db)
    if not err and db.scalar(select(Game).where(func.lower(Game.name) == game.name.lower())):
        err = f"'{game.name}' already exists."
    if err:
        return render(request, "games/form.html", {"bgg_enabled": bgg.enabled(), "game": None, "error": err, "form": await request.form()}, 422)
    db.add(game)
    db.commit()
    flash(request, f"Added {game.name}.")
    await _maybe_bgg_cover(request, db, game)
    nxt = (await request.form()).get("next")
    if nxt == "log":
        return RedirectResponse(f"/plays/new?game={game.id}", status_code=303)
    return RedirectResponse(f"/games/{game.id}", status_code=303)


@router.get("/{game_id}")
def show_game(game_id: int, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404)
    plays = stats.load_plays(db, game_id=game_id)
    rt = ratings.table(db)
    return render(request, "games/show.html", {
        "game": game,
        "rt": rt,
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
    return render(request, "games/form.html", {"bgg_enabled": bgg.enabled(), "game": game})


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
        return render(request, "games/form.html", {"bgg_enabled": bgg.enabled(), "game": db.get(Game, game_id), "error": err,
                                                   "form": await request.form()}, 422)
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
    db.commit()
    flash(request, f"{game.name} {'archived' if game.archived else 'restored'}.")
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
    db.delete(game)
    db.commit()
    media.delete_cover(game_id)
    flash(request, "Game deleted.")
    return RedirectResponse("/games", status_code=303)
