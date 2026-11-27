"""Image handling for play photos and game covers.

Every image is decoded and re-encoded as JPEG: that normalizes formats (incl. iPhone HEIC),
applies EXIF rotation and then drops all metadata (GPS location, device info).
"""

import io
import secrets
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import get_settings

try:  # HEIC/HEIF from iPhones
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

Image.MAX_IMAGE_PIXELS = 60_000_000  # ~60 MP; larger raises DecompressionBombError
ALLOWED = {"JPEG", "PNG", "WEBP", "HEIF", "GIF", "MPO"}
FULL, THUMB, COVER = 2048, 480, 400


class ImageError(ValueError):
    pass


def _dir(name: str) -> Path:
    d = Path(get_settings().data_dir) / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def photo_dir() -> Path:
    return _dir("photos")


def cover_dir() -> Path:
    return _dir("covers")


def _load(data: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(data))
        if im.format not in ALLOWED:
            raise ImageError(f"Unsupported image type ({im.format}). Use JPEG, PNG, WebP or HEIC.")
        im.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as e:
        raise ImageError("That file isn't an image we can read.") from e
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    return im.convert("RGB")


def _jpeg(im: Image.Image, max_side: int, quality: int = 84) -> bytes:
    im = im.copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality, optimize=True, progressive=True)  # no exif= -> metadata dropped
    return out.getvalue()


def save_photo(data: bytes) -> tuple[str, int, int]:
    """Returns (key, width, height) of the stored full-size image."""
    im = _load(data)
    key = secrets.token_hex(16)
    full = _jpeg(im, FULL)
    (photo_dir() / f"{key}.jpg").write_bytes(full)
    (photo_dir() / f"{key}_t.jpg").write_bytes(_jpeg(im, THUMB, 80))
    w, h = Image.open(io.BytesIO(full)).size
    return key, w, h


def photo_path(key: str, thumb: bool = False) -> Path | None:
    if not key.isalnum() or len(key) != 32:
        return None
    p = photo_dir() / f"{key}{'_t' if thumb else ''}.jpg"
    return p if p.is_file() else None


def delete_photo(key: str) -> None:
    for suffix in ("", "_t"):
        (photo_dir() / f"{key}{suffix}.jpg").unlink(missing_ok=True)


def save_cover(game_id: int, data: bytes) -> None:
    (cover_dir() / f"{game_id}.jpg").write_bytes(_jpeg(_load(data), COVER, 82))


def cover_path(game_id: int) -> Path | None:
    p = cover_dir() / f"{int(game_id)}.jpg"
    return p if p.is_file() else None


def delete_cover(game_id: int) -> None:
    (cover_dir() / f"{int(game_id)}.jpg").unlink(missing_ok=True)
