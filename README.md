<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.svg">
    <img src="docs/logo-light.svg" alt="Game Night" width="460">
  </picture>
</p>

<p align="center">
  <b>Log every board game night, from your phone at the table.</b><br>
  Who played, round-by-round scores, score sheets, winners, ratings and rivalries.<br>
  Self-hosted, one Docker container, sign-in through Authentik.
</p>

<p align="center">
  <a href="https://github.com/rbibby53/boardgame-tracker/actions/workflows/docker.yml"><img src="https://github.com/rbibby53/boardgame-tracker/actions/workflows/docker.yml/badge.svg" alt="Build"></a>
  <a href="https://github.com/rbibby53/boardgame-tracker/pkgs/container/boardgame-tracker"><img src="https://img.shields.io/badge/ghcr.io-boardgame--tracker-2f6fdb?logo=docker&logoColor=white" alt="Container image"></a>
  <img src="https://img.shields.io/badge/arch-amd64%20%7C%20arm64-555" alt="amd64 | arm64">
  <img src="https://img.shields.io/badge/python-3.15-3776ab?logo=python&logoColor=white" alt="Python 3.15">
  <img src="https://img.shields.io/badge/SSO-Authentik-fd4b2d" alt="Authentik SSO">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#try-it-in-a-minute">Demo</a> ·
  <a href="#authentik-setup">Authentik</a> ·
  <a href="#reverse-proxy">Reverse proxy</a> ·
  <a href="#roles--permissions">Permissions</a> ·
  <a href="#configuration">Config</a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/dashboard-dark.webp">
    <img src="docs/screenshots/dashboard.webp" alt="Dashboard: latest plays, leaderboard, awards and wins over time" width="900">
  </picture>
</p>

## What it does

<table>
<tr>
<td width="33%" valign="top">

**🎲 Log plays fast**<br>
Pick a game, tap the players, enter scores. You can score by **rounds** (with per-round winners) or with a per-game **score sheet** (Military / Coins / Science…). Totals add up for you, and winners are worked out automatically.

</td>
<td width="33%" valign="top">

**📈 Stats that settle arguments**<br>
Leaderboards, per-game standings and records, **OpenSkill ratings** over time, head-to-head records, nemesis and favorite victim, win streaks, and dated achievements.

</td>
<td width="33%" valign="top">

**🌙 Game nights**<br>
Plays group into evenings automatically, with a champion of the night, clean sweeps, photos and notes.

</td>
</tr>
<tr>
<td valign="top">

**🗳️ "What should we play?"**<br>
Filter by player count and time. Then let it pick, or start a **live vote** everyone answers on their phone. Guests can vote too.

</td>
<td valign="top">

**🔐 Authentik SSO & roles**<br>
OIDC sign-in, with the player list synced from an Authentik group. **Viewer / Player / Manager / Admin** roles map from Authentik groups, with every permission editable.

</td>
<td valign="top">

**📱 Works like an app**<br>
Install it to your home screen. "Log a play" works offline and keeps a draft until you have signal again. Photos are stripped of location data.

</td>
</tr>
<tr>
<td valign="top">

**🧩 BoardGameGeek**<br>
Look up games to fill in details and cover art, sync your **owned collection**, and import your BGG play history.

</td>
<td valign="top">

**🔔 Notifications**<br>
ntfy, Discord, or a generic webhook (Home Assistant) when a play is logged or a vote closes.

</td>
<td valign="top">

**🗄️ Your data, your box**<br>
SQLite with nightly backups (Postgres optional). CSV/JSON export and import. No CDNs, no telemetry.

</td>
</tr>
</table>

## Screenshots

<table>
<tr>
<td width="50%"><img src="docs/screenshots/game.webp" alt="Game page with standings, ratings and score sheet averages"></td>
<td width="50%"><img src="docs/screenshots/player.webp" alt="Player page with achievements, nemesis and ratings"></td>
</tr>
<tr>
<td><img src="docs/screenshots/night.webp" alt="Game night summary with champion"></td>
<td><img src="docs/screenshots/permissions.webp" alt="Roles and permissions admin page"></td>
</tr>
</table>

<p align="center">
  <img src="docs/screenshots/phone-log.webp" alt="Logging a 7 Wonders score sheet on a phone" width="260">&nbsp;&nbsp;
  <img src="docs/screenshots/phone-vote.webp" alt="Voting on what to play" width="260">&nbsp;&nbsp;
  <img src="docs/screenshots/phone-pick.webp" alt="What should we play picker" width="260">
</p>

<sub>Screenshots use the built-in demo data (<code>python -m app.demo</code>). Everyone in them is fictional.</sub>

## Quick start

1. Create `docker-compose.yml`:

   ```yaml
   services:
     boardgames:
       image: ghcr.io/rbibby53/boardgame-tracker:latest
       container_name: boardgames
       restart: unless-stopped
       env_file: .env
       volumes:
         - ./data:/data        # database, photos, covers, backups
       ports:
         - "8000:8000"         # or drop this and attach to your proxy's network
   ```

2. Copy [`.env.example`](.env.example) to `.env`. Fill in `BASE_URL`, `SECRET_KEY` (`openssl rand -hex 32`) and the Authentik settings ([setup below](#authentik-setup)).
3. Run `docker compose up -d`, point your [reverse proxy](#reverse-proxy) at port 8000, and sign in.

Migrations run automatically on start. Upgrading is `docker compose pull && docker compose up -d`.

> [!NOTE]
> GHCR packages start out private. Either make the package public under **Packages → boardgame-tracker → Package settings**, or run `docker login ghcr.io` on the host before pulling.

## Try it in a minute

Kick the tires with demo data and no SSO. This is for **local use only**: `DEV_MODE` lets anyone sign in as anyone.

```bash
docker run -d --name gn-demo -p 8000:8000 -e DEV_MODE=true -e SECRET_KEY=$(openssl rand -hex 32) \
  ghcr.io/rbibby53/boardgame-tracker:latest
docker exec gn-demo python -m app.demo      # 7 fictional players, 9 games, ~100 plays
# open http://localhost:8000 and sign in as "Alex Rivera (admin)"
docker rm -f gn-demo                       # when you're done
```

## Authentik setup

Tested against Authentik 2026.8.

### 1. OIDC sign-in

1. **Applications → Providers → Create → OAuth2/OpenID Provider**
   - **Authorization flow:** `default-provider-authorization-implicit-consent`, or the explicit-consent flow if you want a consent screen.
   - **Client type:** Confidential.
   - **Redirect URI (strict):** `https://games.example.com/auth/callback`, which is `BASE_URL` + `/auth/callback`.
   - **Signing key:** `authentik Self-signed Certificate` (RS256).
   - **Scopes:** `openid`, `profile`, `email`. The default `profile` mapping includes the `groups` claim, which roles use.
   - **Subject mode:** leave it as **Based on the User's hashed ID**, or set `AUTHENTIK_SUB_MODE` to match what you pick.
   - **Grant types:** make sure **authorization_code** is enabled. A provider created through the API starts with none, and sign-in then fails with `invalid_request`.
2. **Applications → Create** with slug `boardgames` (any slug works) and the provider above.
3. **Limit who can sign in:** bind your "Board Gamers" group on the application's **Policy / Group / User Bindings**. `OIDC_ALLOWED_GROUP` is an app-side backstop.
4. Copy the provider's **OpenID Configuration Issuer** into `OIDC_ISSUER`, e.g. `https://auth.example.com/application/o/boardgames/`.

### 2. Player roster sync (read-only token)

1. **Directory → Users → Create Service account**, e.g. `boardgames-sync`.
2. **Directory → Roles → Create** `Board Game Sync` with the global permissions **Can view User** and **Can view Group**.
3. Create a group, e.g. `boardgames-sync`, give it that role, and add the service account.
4. **Directory → Tokens and App passwords → Create** with user = the service account, intent **API**, not expiring. Put the key in `AUTHENTIK_TOKEN`.
   - The token shown when you create the service account is an *app password*, and the API rejects it with `Token invalid/expired`.
5. Set `AUTHENTIK_GROUP` to the group whose members are players. Leave it empty for all users. Service accounts are always skipped.

## Reverse proxy

Run the app on **its own hostname** (e.g. `games.example.com`). Hosting under a path (`example.com/games`) isn't supported, and the app refuses to start with a path in `BASE_URL`.

**Checklist**

- `BASE_URL` is the public **https** URL. Cookies are then `Secure`, and the phone app can be installed.
- The proxy passes the original `Host` and sets `X-Forwarded-Proto` / `X-Forwarded-For`. Caddy, Traefik and Nginx Proxy Manager do this by default.
- **Uploads:** allow at least 30 MB request bodies for photos.
- **Timeouts:** allow about 90 s read timeouts. A first-time BGG collection fetch can take about 45 s.
- **Trusted proxies:** by default the image only trusts `X-Forwarded-*` from private addresses (`127.0.0.1, 10/8, 172.16/12, 192.168/16, fc00::/7`). To narrow that, set `FORWARDED_ALLOW_IPS`, e.g. `172.18.0.0/16`.
- **Opening the app by IP or another name is safe.** Sign-in hops to `BASE_URL` first, so Authentik's callback always matches.

The app was tested end to end behind a TLS-terminating proxy with Authentik:

- HTTPS sign-in
- `Secure` / `HttpOnly` / `SameSite=Lax` session cookie
- forwarded client IP
- service worker over HTTPS

The snippets below are the standard configs for each proxy.

<details>
<summary><b>Caddy</b></summary>

```caddyfile
games.example.com {
	reverse_proxy boardgames:8000
}
```
</details>

<details>
<summary><b>Traefik</b> (Docker labels)</summary>

```yaml
services:
  boardgames:
    # ...as in Quick start, without `ports:`, on Traefik's network
    labels:
      - traefik.enable=true
      - traefik.http.routers.boardgames.rule=Host(`games.example.com`)
      - traefik.http.routers.boardgames.entrypoints=websecure      # your HTTPS entrypoint
      - traefik.http.routers.boardgames.tls.certresolver=letsencrypt  # your resolver
      - traefik.http.services.boardgames.loadbalancer.server.port=8000
```
</details>

<details>
<summary><b>nginx</b></summary>

```nginx
server {
    listen 443 ssl;
    server_name games.example.com;
    ssl_certificate     /etc/ssl/games.example.com.crt;
    ssl_certificate_key /etc/ssl/games.example.com.key;

    client_max_body_size 30m;              # photo uploads

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
        proxy_read_timeout 90s;            # BGG collection sync
    }
}
```
</details>

<details>
<summary><b>Nginx Proxy Manager</b></summary>

- **Details tab:**
  - Scheme `http`.
  - Forward hostname `boardgames`, or the host's IP.
  - Port `8000`.
- **SSL tab:** pick a certificate and turn on **Force SSL**.
- **Advanced tab:**

  ```nginx
  client_max_body_size 30m;
  proxy_read_timeout 90s;
  ```
</details>

## Roles & permissions

Everyone signed in can view everything. What else they can do depends on their role, and you can edit all of it under **Admin → Permissions**.

| Permission | Viewer | Player | Manager | Admin |
|---|:-:|:-:|:-:|:-:|
| Vote on what to play | ✓ | ✓ | ✓ | ✓ |
| Log plays, and edit plays they logged or played in | | ✓ | ✓ | ✓ |
| Add photos to plays they can edit | | ✓ | ✓ | ✓ |
| Add and edit games (score sheets, covers, BGG lookup) | | ✓ | ✓ | ✓ |
| Add and rename guest players | | ✓ | ✓ | ✓ |
| Start votes | | ✓ | ✓ | ✓ |
| Edit game night titles and notes | | ✓ | ✓ | ✓ |
| Export CSV / JSON | | ✓ | ✓ | ✓ |
| Edit or delete any play | | | ✓ | ✓ |
| Archive and delete games | | | ✓ | ✓ |
| Admin: sync, import, backups, BGG collection, permissions | | | | ✓ |

<sub>These are the defaults. Every cell except the Admin column can be changed.</sub>

**How someone gets their role**, first match wins:

1. Member of `OIDC_ADMIN_GROUP`: **admin**, always. This can't be overridden, so you can't lock yourself out.
2. A role set for that person on the Permissions page.
3. The highest role among their Authentik groups, using mappings like `Board Game Managers → Manager`.
4. The default role, which is **Player** unless you change it.

Groups are read at sign-in and on every roster sync. The page refuses any change that would leave nobody with admin.

## Configuration

All settings are environment variables; see [`.env.example`](.env.example).

<details open>
<summary><b>Required</b></summary>

| Variable | Notes |
|---|---|
| `BASE_URL` | Public URL, e.g. `https://games.example.com`. No path. |
| `SECRET_KEY` | At least 32 characters (`openssl rand -hex 32`). Signs session cookies. |
| `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | From the Authentik provider. |

</details>

<details>
<summary><b>Everything else</b></summary>

| Variable | Default | Notes |
|---|---|---|
| `TIMEZONE` | `America/New_York` | Default time for new plays, game night boundaries, and how timestamps are shown. |
| `APP_NAME` | `Game Night` | Shown in the header and on the installed app. |
| `DATABASE_URL` | `sqlite:////data/boardgames.db` | Or `postgresql+psycopg://user:pass@host/db`. |
| `OIDC_ADMIN_GROUP` | — | Members are always admins. |
| `OIDC_ALLOWED_GROUP` | — | Optional sign-in gate. Prefer an Authentik binding. |
| `AUTHENTIK_URL` / `AUTHENTIK_TOKEN` | — | Enables roster sync. |
| `AUTHENTIK_GROUP` | — | Which users become players. |
| `AUTHENTIK_SUB_MODE` | `hashed_user_id` | Must match the provider's subject mode. |
| `AUTHENTIK_SYNC_INTERVAL_MINUTES` | `60` | `0` turns off the schedule. **Sync now** still works. |
| `FORWARDED_ALLOW_IPS` | private ranges | Proxies allowed to set `X-Forwarded-*`. Accepts subnets (CIDR). |
| `DATA_DIR` | `/data` | Photos, covers, backups and staged imports. |
| `MAX_PHOTO_MB` | `25` | Per-photo upload limit. |
| `NIGHT_CUTOFF_HOUR` | `5` | Plays before this hour count toward the previous night. |
| `NTFY_URL` / `NTFY_TOKEN` | — | Full topic URL, e.g. `https://ntfy.example.com/gamenight`. |
| `DISCORD_WEBHOOK_URL` | — | Discord channel webhook (mentions disabled). |
| `NOTIFY_WEBHOOK_URL` | — | JSON POST per play or closed vote (e.g. a Home Assistant webhook). |
| `BGG_TOKEN` | — | BoardGameGeek API token. BGG requires a [registered app](https://boardgamegeek.com/applications). |
| `BACKUP_HOUR` / `BACKUP_KEEP` | `3` / `14` | Nightly SQLite backup time and how many to keep (`0` = off). |
| `DEV_MODE` | `false` | Skips SSO with a user picker. **Local testing only.** |

</details>

## Guides

<details>
<summary><b>Ratings</b></summary>

[OpenSkill](https://github.com/vivekjoshy/openskill.py) (Plackett–Luce), recomputed from full history, so fixing an old play fixes everything after it.

- **Displayed number:** `1000 + 40 × (μ − 3σ)`. New players start at 1000.
- **Provisional:** a "?" marks fewer than 5 rated plays.
- **Not rated:** co-op plays and plays where everyone tied.
- **Teams:** teammates move together.
</details>

<details>
<summary><b>Importing plays</b></summary>

**Admin → Import plays** accepts:

- **CSV:** this app's export, or any sheet with `played_at` (or `date`), `game` and `player` columns, plus optional `score`, `winner`, `team`, `location`, `duration_min`, `notes` and `play_id`.
- **BGG plays XML file**, or a **BGG username** (needs `BGG_TOKEN`).

You get a preview before anything is written. Players match by username, then name; anyone unknown becomes a guest. Re-running the same import is safe.
</details>

<details>
<summary><b>Mini-games and variants (Jackbox packs, Uno Flip…)</b></summary>

A game can be **Part of** another game, one level deep. There are two kinds:

**Boxes of games** (a Jackbox Party Pack): tick *Box of games, not played itself* on the box, then add each game in it with **Part of** set to the box.

- The box is never offered in **Log a play** or **What should we play?**. Its mini-games are, grouped under it, each with its own player count and length.
- The box page lists its mini-games, with standings and recent plays across all of them. Scores, records and ratings stay on each mini-game's page.
- Mini-games follow the box's shelf status. Archiving or restoring the box does the same to them; you can still archive one on its own.

**Variants** (Uno Flip and Uno Attack of Uno, Ticket to Ride: Europe…): set **Part of** to the base game.

- The base game and each variant are played, rated and shelved separately, with their own scoring, score sheet and player counts.
- The base game's page adds a *Variants* list and *All versions* standings. Filtering plays by the base game includes its variants.

Deleting a box or base game keeps its mini-games or variants as standalone games.
</details>

<details>
<summary><b>Keeping score by round (Mexican Train, Uno, Hearts…)</b></summary>

Tick **Keep score by round** (on by default for games set up that way). For a points game you then choose:

- **Who won each round:** no points. Tap the player who won each round; whoever wins the most rounds wins the game.
- **Points:** type everyone's points per round; totals and the winner (lowest or highest, per the game) fill in themselves.

The form remembers which one you used last time for that game. A live line above the rounds shows who's leading. Give a game **Round names** (e.g. Mexican Train: `Double 12` … `Double blank`) and every round is labelled, with **+ Next round: Double 11** to add the next one. Overriding totals, picking the winner by hand and teams are under **More options**.

Your scores are saved on the phone as you go, so a locked screen or lost signal doesn't lose the game.
</details>

<details>
<summary><b>Expansions, favorites and finding games</b></summary>

- **Expansions:** list a game's expansions and extensions on its Edit page and tick the ones you have. When you log a play, tap the ones you're playing with. The ones you own come first, and it remembers what you used last time. The play and the game page show what was used.
- **Favorites:** tap ☆ on a game (in the list or on its page). Each person's favorites come first in **Log a play**, and the games list and picker can show only them. Starring a box of games (a Jackbox pack) covers its mini-games.
- **Search:** the games list has a search box, and **Log a play** has a "Type to find a game" box above the game list. Your recently played games are listed first.
</details>

<details>
<summary><b>BGG collection sync</b></summary>

**Admin → Sync BGG collection** reads the board games a BGG user marks **Own** (expansions skipped). The preview shows exactly what will change:

- **New games** are added.
- **Existing games** only get *blank* fields filled in; your edits are kept.
- **Games no longer in the collection** can be marked "not on our shelf", archived, or left alone.
- **Covers** download in the background.

The first fetch can take up to a minute while BGG prepares it; if it times out, run it again. The picker defaults to games on your shelf.
</details>

<details>
<summary><b>Installing on your phone</b></summary>

Open the site over HTTPS, then use **Share → Add to Home Screen** (iOS) or **Install app** (Android). If you lose signal mid-game, the play is kept as a draft on the phone. Open **Log a play** once you're back online and tap Save.
</details>

<details>
<summary><b>Backups &amp; restore</b></summary>

With SQLite, a gzipped, integrity-checked snapshot lands in `data/backups/` every night. You can also run or download one from **Admin → Backups**. Back up all of `./data/` off the box, because photos and covers live there too.

**To restore:**

1. Stop the container.
2. Run `gunzip -c data/backups/boardgames-<stamp>.db.gz > data/boardgames.db`.
3. Delete any `boardgames.db-wal` / `-shm` files.
4. Start the container again.

With Postgres, schedule `pg_dump` yourself.
</details>

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q

export SECRET_KEY=$(openssl rand -hex 32) DEV_MODE=true DATABASE_URL=sqlite:///./dev.db DATA_DIR=./dev-data
.venv/bin/python -m app.demo            # optional demo data
.venv/bin/uvicorn app.main:app --reload
```

Schema changes go through Alembic (`.venv/bin/alembic revision --autogenerate -m "..."`) and run automatically at startup.

**Built with** FastAPI, SQLAlchemy 2 and Alembic, Authlib, OpenSkill, Pillow (+ pillow-heif), Jinja templates with a little vanilla JS, and Chart.js (vendored).
