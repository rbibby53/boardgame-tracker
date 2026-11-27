import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.datastructures import UploadFile  # what request.form() returns
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import media
from ..auth import can, current_user, require, verify_csrf
from ..config import get_settings
from ..db import get_db
from ..models import Game, PlayPhoto, Player
from ..templating import flash
from .plays import _get_play, can_edit

router = APIRouter()
MAX_PER_UPLOAD = 10
CACHE = {"Cache-Control": "private, max-age=31536000, immutable"}


async def _read_limited(f: UploadFile, limit: int) -> bytes:
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise media.ImageError(f"{f.filename} is larger than {limit // 1_000_000} MB.")
    return data


@router.post("/plays/{play_id}/photos", dependencies=[Depends(verify_csrf)])
async def upload_photos(play_id: int, request: Request, user: Player = Depends(require("upload_photos")),
                        db: Session = Depends(get_db)):
    play = _get_play(db, play_id)
    if not can_edit(request, user, play):
        raise HTTPException(403, "Only people in this play (or admins) can add photos.")
    form = await request.form(max_files=MAX_PER_UPLOAD + 1)
    files = [f for f in form.getlist("photos") if isinstance(f, UploadFile) and f.filename]
    if not files:
        flash(request, "Choose at least one photo.", "err")
        return RedirectResponse(f"/plays/{play_id}", status_code=303)
    if len(files) > MAX_PER_UPLOAD:
        flash(request, f"Up to {MAX_PER_UPLOAD} photos at a time.", "err")
        return RedirectResponse(f"/plays/{play_id}", status_code=303)

    limit = get_settings().max_photo_mb * 1_000_000
    added, errors = 0, []
    for f in files:
        try:
            data = await _read_limited(f, limit)
            key, w, h = await asyncio.to_thread(media.save_photo, data)
        except media.ImageError as e:
            errors.append(str(e))
            continue
        db.add(PlayPhoto(play_id=play.id, key=key, width=w, height=h, uploaded_by_id=user.id))
        added += 1
    db.commit()
    if added:
        flash(request, f"Added {added} photo{'s' if added != 1 else ''}." + (f" Skipped: {' '.join(errors)}" if errors else ""),
              "err" if errors else "ok")
    else:
        flash(request, " ".join(errors) or "No photos added.", "err")
    return RedirectResponse(f"/plays/{play_id}#photos", status_code=303)


@router.get("/photos/{key}")
def get_photo(key: str, user: Player = Depends(current_user)):
    p = media.photo_path(key)
    if p is None:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers=CACHE)


@router.get("/photos/{key}/thumb")
def get_thumb(key: str, user: Player = Depends(current_user)):
    p = media.photo_path(key, thumb=True)
    if p is None:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers=CACHE)


@router.post("/photos/{key}/delete", dependencies=[Depends(verify_csrf)])
def delete_photo(key: str, request: Request, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    ph = db.scalar(select(PlayPhoto).where(PlayPhoto.key == key))
    if ph is None:
        raise HTTPException(404)
    play = _get_play(db, ph.play_id)
    if not (can_edit(request, user, play) or (ph.uploaded_by_id == user.id and can(request, "upload_photos"))):
        raise HTTPException(403)
    db.delete(ph)
    db.commit()
    media.delete_photo(key)
    flash(request, "Photo deleted.")
    return RedirectResponse(f"/plays/{play.id}#photos", status_code=303)


@router.get("/covers/{game_id}")
def get_cover(game_id: int, user: Player = Depends(current_user), db: Session = Depends(get_db)):
    p = media.cover_path(game_id)
    if p is None:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/games/{game_id}/cover", dependencies=[Depends(verify_csrf)])
async def upload_cover(game_id: int, request: Request, user: Player = Depends(require("manage_games")),
                       db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404)
    form = await request.form(max_files=2)
    f = form.get("cover")
    if form.get("remove"):
        media.delete_cover(game.id)
        game.has_cover = False
        db.commit()
        flash(request, "Cover removed.")
        return RedirectResponse(f"/games/{game.id}/edit", status_code=303)
    if not isinstance(f, UploadFile) or not f.filename:
        flash(request, "Choose an image.", "err")
        return RedirectResponse(f"/games/{game.id}/edit", status_code=303)
    try:
        data = await _read_limited(f, get_settings().max_photo_mb * 1_000_000)
        await asyncio.to_thread(media.save_cover, game.id, data)
    except media.ImageError as e:
        flash(request, str(e), "err")
        return RedirectResponse(f"/games/{game.id}/edit", status_code=303)
    game.has_cover = True
    db.commit()
    flash(request, "Cover updated.")
    return RedirectResponse(f"/games/{game.id}", status_code=303)
