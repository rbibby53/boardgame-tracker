import io
import json

from PIL import Image
from sqlalchemy import select

from app import media
from app.models import Game, PlayPhoto, Player

from .conftest import login


def _jpeg_with_gps(size=(3000, 1000)) -> bytes:
    im = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90 CW on display
    exif[0x8825] = {1: "N", 2: (33.0, 57.0, 1.0)}  # GPS IFD
    exif[0x010F] = "PhoneMaker"
    out = io.BytesIO()
    im.save(out, "JPEG", exif=exif)
    return out.getvalue()


def _make_play(client, db):
    csrf = login(client, "Ryan")
    ryan = db.scalar(select(Player).where(Player.display_name == "Ryan"))
    g = Game(name="Azul", scoring="high")
    db.add(g)
    db.commit()
    r = client.post("/plays", data={"csrf": csrf, "game_id": str(g.id), "played_at": "2026-11-20T19:30",
                                    "payload": json.dumps({"players": [{"player_id": ryan.id, "score": 1}]})},
                    follow_redirects=False)
    return csrf, int(r.headers["location"].rsplit("/", 1)[1]), g


def test_upload_strips_metadata_and_rotates(client, db):
    csrf, play_id, _ = _make_play(client, db)
    heic = io.BytesIO()
    Image.new("RGB", (400, 300), (0, 90, 200)).save(heic, "HEIF")
    r = client.post(f"/plays/{play_id}/photos", data={"csrf": csrf},
                    files=[("photos", ("board.jpg", _jpeg_with_gps(), "image/jpeg")),
                           ("photos", ("iphone.heic", heic.getvalue(), "image/heic")),
                           ("photos", ("notes.txt", b"not an image", "text/plain"))],
                    follow_redirects=False)
    assert r.status_code == 303
    photos = db.scalars(select(PlayPhoto).order_by(PlayPhoto.id)).all()
    assert len(photos) == 2  # the text file was rejected

    full = client.get(f"/photos/{photos[0].key}")
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
    im = Image.open(io.BytesIO(full.content))
    assert im.size == (683, 2048)  # rotated to portrait and capped at 2048
    assert not im.getexif()  # no GPS, no camera make
    assert client.get(f"/photos/{photos[0].key}/thumb").status_code == 200
    assert "/photos/" in client.get(f"/plays/{play_id}").text

    # photos need a session
    client.post("/logout", data={"csrf": csrf})
    assert client.get(f"/photos/{photos[0].key}", follow_redirects=False).status_code in (303, 401)


def test_deleting_play_removes_files(client, db):
    csrf, play_id, _ = _make_play(client, db)
    client.post(f"/plays/{play_id}/photos", data={"csrf": csrf},
                files=[("photos", ("a.jpg", _jpeg_with_gps((50, 50)), "image/jpeg"))])
    key = db.scalar(select(PlayPhoto.key))
    assert media.photo_path(key) is not None
    client.post(f"/plays/{play_id}/delete", data={"csrf": csrf})
    assert media.photo_path(key) is None and media.photo_path(key, thumb=True) is None


def test_cover_upload_and_path_safety(client, db):
    csrf, _, g = _make_play(client, db)
    r = client.post(f"/games/{g.id}/cover", data={"csrf": csrf},
                    files={"cover": ("c.png", _jpeg_with_gps((800, 800)), "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Game, g.id).has_cover
    assert Image.open(io.BytesIO(client.get(f"/covers/{g.id}").content)).size == (400, 400)
    assert client.get("/photos/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert media.photo_path("../../etc/passwd") is None
