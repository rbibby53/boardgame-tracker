"""Optional notifications when a play is logged: ntfy, Discord webhook, generic JSON webhook."""

import logging

import httpx

from .config import Settings
from .models import Play
from .scoring import fmt_score

log = logging.getLogger(__name__)


def summarize(play: Play, base_url: str) -> dict:
    """Plain data snapshot (safe to use after the DB session closes)."""
    seats = play.ordered_seats
    coop = play.game.scoring == "coop"
    winners = [s.player.display_name for s in seats if s.is_winner]
    if coop:
        headline = f"{'🎉 The team beat' if play.coop_won else '💀 The game beat the team in'} {play.game.name}"
    elif winners:
        headline = f"🏆 {' & '.join(winners)} won {play.game.name}"
    else:
        headline = f"{play.game.name} logged"
    others = ", ".join(
        f"{s.player.display_name}{' ' + fmt_score(s.score) if s.score is not None else ''}" for s in seats
    )
    return {
        "event": "play_logged",
        "play_id": play.id,
        "game": play.game.name,
        "played_at": play.played_at.isoformat(),
        "location": play.location,
        "headline": headline,
        "body": others,
        "url": f"{base_url}/plays/{play.id}",
        "players": [{"name": s.player.display_name, "score": s.score, "place": s.place, "winner": s.is_winner}
                    for s in seats],
        "winners": winners,
        "coop_won": play.coop_won,
    }


def send(settings: Settings, msg: dict) -> list[str]:
    """Send to every configured target. Returns a list of error strings (empty = all good)."""
    errors: list[str] = []
    with httpx.Client(timeout=10) as client:
        if settings.ntfy_url:
            # JSON publishing: POST to the server root with the topic in the body (handles UTF-8 titles).
            server, _, topic = settings.ntfy_url.rstrip("/").rpartition("/")
            headers = {"Authorization": f"Bearer {settings.ntfy_token}"} if settings.ntfy_token else {}
            errors += _post(client, "ntfy", server + "/", headers=headers, json={
                "topic": topic, "title": msg["headline"], "message": msg["body"] or msg["headline"],
                "click": msg["url"], "tags": ["game_die"],
            })
        if settings.discord_webhook_url:
            text = f"**{msg['headline']}**\n{msg['body']}\n<{msg['url']}>"[:1900]
            errors += _post(client, "Discord", settings.discord_webhook_url,
                            json={"content": text, "allowed_mentions": {"parse": []}})
        if settings.notify_webhook_url:
            errors += _post(client, "webhook", settings.notify_webhook_url, json=msg)
    return errors


def _post(client: httpx.Client, name: str, url: str, **kw) -> list[str]:
    try:
        r = client.post(url, **kw)
        if r.status_code >= 300:
            return [f"{name}: HTTP {r.status_code} {r.text[:200]}"]
    except httpx.HTTPError as e:
        return [f"{name}: {e}"]
    return []


def send_logged(settings: Settings, msg: dict) -> None:
    for err in send(settings, msg):
        log.warning("Notification failed: %s", err)
