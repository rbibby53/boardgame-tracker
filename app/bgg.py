"""BoardGameGeek XML API2 client (search + game details + cover image).

BGG requires a registered application token (https://boardgamegeek.com/applications),
sent as `Authorization: Bearer <token>`. Data is for non-commercial use; see BGG's XML API terms.
"""

import time
import xml.etree.ElementTree as ET  # types only; parsing goes through defusedxml
from urllib.parse import urlparse

import httpx
from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from .config import get_settings

BASE = "https://boardgamegeek.com/xmlapi2"
IMAGE_HOSTS = ("cf.geekdo-images.com",)


class BggError(Exception):
    pass


def enabled() -> bool:
    return bool(get_settings().bgg_token)


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=15,
        headers={"Authorization": f"Bearer {get_settings().bgg_token}", "Accept": "application/xml",
                 "User-Agent": "game-night-tracker (self-hosted)"},
        follow_redirects=True,
    )


def _get(path: str, params: dict, *, sleep=time.sleep) -> ET.Element:
    if not enabled():
        raise BggError("BoardGameGeek lookup isn't configured (BGG_TOKEN).")
    delay = 2.0
    with _client() as c:
        for _ in range(5):
            try:
                r = c.get(f"{BASE}/{path}", params=params)
            except httpx.HTTPError as e:
                raise BggError(f"Couldn't reach BoardGameGeek: {e}") from e
            if r.status_code in (202, 429, 503):  # queued / rate limited: back off and retry
                sleep(delay)
                delay *= 2
                continue
            if r.status_code in (401, 403):
                raise BggError("BoardGameGeek rejected the token. Check BGG_TOKEN.")
            if r.status_code != 200:
                raise BggError(f"BoardGameGeek returned HTTP {r.status_code}.")
            try:
                return SafeET.fromstring(r.content)
            except (ET.ParseError, DefusedXmlException) as e:
                raise BggError("BoardGameGeek sent a response we couldn't read.") from e
    raise BggError("BoardGameGeek is busy. Try again in a minute.")


def _val(item: ET.Element, tag: str) -> str | None:
    el = item.find(tag)
    return el.get("value") if el is not None else None


def _int(v: str | None) -> int | None:
    try:
        n = int(v) if v else None
    except ValueError:
        return None
    return n if n else None  # BGG uses 0 for "unknown"


def search(query: str, **kw) -> list[dict]:
    query = query.strip()
    if len(query) < 2:
        return []
    root = _get("search", {"query": query, "type": "boardgame"}, **kw)
    out = []
    for item in root.findall("item"):
        name = item.find("name")
        if name is None or not item.get("id"):
            continue
        out.append({"id": int(item.get("id")), "name": name.get("value"), "year": _int(_val(item, "yearpublished"))})
    q = query.lower()
    out.sort(key=lambda r: (r["name"].lower() != q, not r["name"].lower().startswith(q), -(r["year"] or 0)))
    return out[:25]


def thing(bgg_id: int, **kw) -> dict:
    root = _get("thing", {"id": str(int(bgg_id))}, **kw)
    item = root.find("item")
    if item is None:
        raise BggError("No game with that BoardGameGeek ID.")
    primary = next((n.get("value") for n in item.findall("name") if n.get("type") == "primary"), None)
    play_time = _int(_val(item, "playingtime")) or _int(_val(item, "maxplaytime"))
    image = (item.findtext("image") or item.findtext("thumbnail") or "").strip() or None
    return {
        "id": int(item.get("id")),
        "name": primary,
        "year": _int(_val(item, "yearpublished")),
        "min_players": _int(_val(item, "minplayers")),
        "max_players": _int(_val(item, "maxplayers")),
        "play_time_min": play_time,
        "image": image,
    }


def fetch_image(url: str, max_bytes: int = 15_000_000) -> bytes:
    """Download a cover from BGG's image CDN only (never an arbitrary URL)."""
    u = urlparse(url)
    if u.scheme != "https" or u.hostname not in IMAGE_HOSTS:
        raise BggError("Unexpected image location from BoardGameGeek.")
    with httpx.Client(timeout=20, follow_redirects=False) as c, c.stream("GET", url) as r:
        if r.status_code != 200:
            raise BggError(f"Couldn't download the cover (HTTP {r.status_code}).")
        data = b""
        for chunk in r.iter_bytes():
            data += chunk
            if len(data) > max_bytes:
                raise BggError("Cover image is too large.")
    return data
