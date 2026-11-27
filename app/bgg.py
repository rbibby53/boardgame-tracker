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


def _get(path: str, params: dict, *, sleep=time.sleep, delays=(2, 4, 8, 16)) -> ET.Element:
    if not enabled():
        raise BggError("BoardGameGeek lookup isn't configured (BGG_TOKEN).")
    waits = list(delays)
    with _client() as c:
        while True:
            try:
                r = c.get(f"{BASE}/{path}", params=params)
            except httpx.HTTPError as e:
                raise BggError(f"Couldn't reach BoardGameGeek: {e}") from e
            if r.status_code in (202, 429, 503):  # queued / rate limited: back off and retry
                if not waits:
                    break
                sleep(waits.pop(0))
                continue
            if r.status_code in (401, 403):
                raise BggError("BoardGameGeek rejected the token. Check BGG_TOKEN.")
            if r.status_code != 200:
                raise BggError(f"BoardGameGeek returned HTTP {r.status_code}.")
            try:
                root = SafeET.fromstring(r.content)
            except (ET.ParseError, DefusedXmlException) as e:
                raise BggError("BoardGameGeek sent a response we couldn't read.") from e
            err = root.find(".//error/message") if root.tag == "errors" else None
            if err is not None:
                raise BggError(f"BoardGameGeek: {err.text}")
            return root
    raise BggError("BoardGameGeek is still preparing that. Try again in a minute.")


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


def collection(username: str, **kw) -> list[dict]:
    """Board games (not expansions) marked as owned in a BGG user's collection."""
    username = username.strip()
    if not username:
        raise BggError("Enter a BoardGameGeek username.")
    kw.setdefault("delays", (2, 3, 5, 8, 12, 15))  # first request is usually 202 while BGG builds it
    root = _get("collection", {"username": username, "own": "1", "subtype": "boardgame",
                               "excludesubtype": "boardgameexpansion", "stats": "1"}, **kw)
    out: dict[int, dict] = {}
    for item in root.findall("item"):
        oid = item.get("objectid") or ""
        if not oid.isdigit() or item.get("subtype", "boardgame") != "boardgame":
            continue
        status = item.find("status")
        if status is not None and status.get("own") not in (None, "1"):
            continue
        stats = item.find("stats")
        st = stats.attrib if stats is not None else {}
        name = (item.findtext("name") or "").strip()
        if not name:
            continue
        entry = {
            "bgg_id": int(oid),
            "name": name[:200],
            "year": _int((item.findtext("yearpublished") or "").strip()),
            "min_players": _int(st.get("minplayers")),
            "max_players": _int(st.get("maxplayers")),
            "play_time_min": _int(st.get("playingtime")) or _int(st.get("maxplaytime")),
            "image": (item.findtext("image") or item.findtext("thumbnail") or "").strip() or None,
        }
        prev = out.get(int(oid))
        if prev is None:
            out[int(oid)] = entry
        else:  # another copy of the same game: only fill gaps
            for k, v in entry.items():
                if prev.get(k) is None and v is not None:
                    prev[k] = v
    return sorted(out.values(), key=lambda g: g["name"].lower())
