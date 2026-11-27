# Game Night: board game score tracker

A self-hosted web app for logging board game plays: who played, round-by-round scores, who won each round, final scores, and winners. It also tracks standings and trends over time. Login is SSO via **Authentik** (OIDC), and the player roster is pulled from Authentik's API.

It ships as one container, with SQLite by default (Postgres optional), no external CDNs, and no telemetry.

## Features

- **Log a play** from your phone at the table:
  - Pick the game and tap the players. You can add guests inline, and "same players as last time" fills the lineup in one tap.
  - **Round-by-round scoring** with per-round winners. Totals auto-sum, and you can override any total by hand.
  - Location, length, expansions/variant, teams and notes.
- **Scoring types per game:**
  - Highest wins
  - Lowest wins (Hearts, golf)
  - Win/loss with no points (Chess; can be decided by most rounds won)
  - Co-op (Pandemic: the team wins or loses together)
- **Winners are automatic** (ties share first), or pick them manually.
- **Stats:**
  - Overall leaderboard (90-day and all-time) and per-game standings (win %, average and best score).
  - Records (best score, biggest margin).
  - Per-player breakdown by game, plus head-to-head records against each opponent.
- **Charts:** scores over time and cumulative wins over time. Click a point to open that play.
- **SSO:**
  - Authentik OIDC with PKCE.
  - An admin group from the `groups` claim, plus an optional allowed-group gate.
- **Roster sync:** pulls members of an Authentik group on a schedule or on demand.
  - Users removed from the group are deactivated, but their history is kept.
  - A sync that returns zero users is refused, so a bad filter can't wipe your roster.
- **Guests** are local players without a login. When a guest later gets an account, an admin can merge the guest into it.
- **Score sheets**: give a game its scoring categories (e.g. 7 Wonders: Military, Coins, Wonders, Science). The play form becomes a category × player grid, and the game page shows per-category averages and records.
- **Skill ratings** (OpenSkill): accounts for who you beat, handles ties and teams, and is tracked per game and overall with rating-over-time charts.
- **Game nights**: plays are grouped by evening, with a champion of the night, clean sweeps, notes and photos.
- **Streaks, rivals and achievements**: hot streaks, nemesis and favorite victim, "always the bridesmaid", plus dated achievements per player.
- **What should we play?**: filter by player count and time, sort by least recently played or fewest plays, or let it pick for you.
- **Vote on what to play**: start a vote from the picker's list. Everyone taps every game they'd play, and results update live on each phone. Ties go to the game played longest ago. You can vote on behalf of guests who don't have an account.
- **BGG collection sync**: pull the games you own on BoardGameGeek into the app (preview first), fill in missing details and covers, and flag games you've sold. The picker then defaults to games on your shelf.
- **Photos**: attach board photos to a play. Location and other metadata are stripped, and iPhone HEIC is supported.
- **BoardGameGeek**: look up a game to auto-fill player counts, play time, year and cover art. You can also import your BGG play history.
- **Installable app (PWA)**: add it to your phone's home screen. "Log a play" works offline and keeps a draft until you're back online.
- **Notifications** via ntfy, Discord, or a generic webhook (e.g. Home Assistant) when a play is logged.
- **Import** from CSV or BGG, with a dry-run preview first.
- **Export** plays as CSV or JSON.
- **Daily SQLite backups** with rotation.

## Quick start

```bash
cp .env.example .env        # fill it in (see Authentik setup below)
openssl rand -hex 32        # paste into SECRET_KEY
docker compose up -d
```

Image: `ghcr.io/<owner>/<repo>:latest` (amd64 + arm64). It's built by GitHub Actions on every push to `main` and every `v*` tag.

The app listens on `:8000`. Put it behind your reverse proxy for TLS and set `BASE_URL` to the public HTTPS URL. Cookies are marked `Secure` automatically when `BASE_URL` is `https://`.

## Authentik setup

Tested against Authentik 2026.8.

### 1. OIDC login

1. **Applications → Providers → Create → OAuth2/OpenID Provider**
   - Authorization flow: `default-provider-authorization-implicit-consent` (or explicit if you want a consent screen)
   - Client type: **Confidential**
   - Redirect URI (strict): `https://games.example.com/auth/callback`
   - Signing key: `authentik Self-signed Certificate` (RS256)
   - Scopes: `openid`, `profile`, `email`. The default `profile` mapping includes the `groups` claim.
   - Subject mode: leave as **Based on the User's hashed ID**, or set `AUTHENTIK_SUB_MODE` to match whatever you choose.
   - Grant types: make sure **authorization_code** is enabled. A provider created through the API starts with *no* grant types, and logins then fail with `invalid_request`.
2. **Applications → Create**, using slug `boardgames` (any slug works) and the provider above.
3. **Limit who can log in:** on the application, use **Policy / Group / User Bindings** and bind your "Board Gamers" group. This is the recommended gate. `OIDC_ALLOWED_GROUP` is an app-side backstop.
4. Copy the **OpenID Configuration Issuer** URL from the provider page into `OIDC_ISSUER`. It looks like `https://auth.example.com/application/o/boardgames/`.

### 2. Roster sync (read-only API token)

1. **Directory → Users → Create Service account**, e.g. `boardgames-sync`.
2. **Directory → Roles → Create** a role, e.g. `Board Game Sync`, and give it the global permissions **Can view User** (`authentik_core.view_user`) and **Can view Group** (`authentik_core.view_group`).
3. Create a group (e.g. `boardgames-sync`), assign it the role, and add the service account to it.
4. **Directory → Tokens and App passwords → Create**: user = the service account, intent **API**, not expiring. Copy the key into `AUTHENTIK_TOKEN`.
   - The token Authentik shows when you create the service account is an *app password*. The API rejects it with `Token invalid/expired`.
5. Set `AUTHENTIK_GROUP` to the group whose members should appear as players. Leave it empty to sync all internal and external users. Service accounts are always excluded.

Admins in the app are members of `OIDC_ADMIN_GROUP`. It's checked at login (from the `groups` claim) and on every sync. If you leave it unset, Authentik superusers become admins through sync.

## Configuration

All settings are environment variables; see [`.env.example`](.env.example).

| Variable | Default | Notes |
|---|---|---|
| `BASE_URL` | `http://localhost:8000` | Public URL. The redirect URI is `BASE_URL/auth/callback`. |
| `SECRET_KEY` | — | **Required**, at least 32 characters. Signs session cookies. |
| `TIMEZONE` | `America/New_York` | Default time for new plays, and how audit timestamps are shown. |
| `DATABASE_URL` | `sqlite:////data/boardgames.db` | Or `postgresql+psycopg://user:pass@host/db`. |
| `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | — | **Required** unless `DEV_MODE=true`. |
| `OIDC_ADMIN_GROUP` | — | Members of this group are app admins. |
| `OIDC_ALLOWED_GROUP` | — | Optional login gate. |
| `AUTHENTIK_URL` / `AUTHENTIK_TOKEN` | — | Enables roster sync. |
| `AUTHENTIK_GROUP` | — | Which users become players. |
| `AUTHENTIK_SUB_MODE` | `hashed_user_id` | Must match the provider's subject mode (`hashed_user_id`, `user_id`, `user_uuid`, `user_username`, `user_email`, `user_upn`). |
| `AUTHENTIK_SYNC_INTERVAL_MINUTES` | `60` | `0` disables the background sync. The **Sync now** button still works. |
| `DATA_DIR` | `/data` | Photos, covers, backups and staged imports live here. |
| `MAX_PHOTO_MB` | `25` | Upload limit per photo. |
| `NIGHT_CUTOFF_HOUR` | `5` | Plays before this local hour count toward the previous evening's game night. |
| `NTFY_URL` / `NTFY_TOKEN` | — | Full topic URL, e.g. `https://ntfy.example.com/gamenight`. The token is optional (sent as a Bearer header). |
| `DISCORD_WEBHOOK_URL` | — | Discord channel webhook. Mentions are disabled. |
| `NOTIFY_WEBHOOK_URL` | — | Receives a JSON POST with the play, players, scores and winners (e.g. a Home Assistant webhook trigger). |
| `BGG_TOKEN` | — | BoardGameGeek API token. BGG requires a registered application for API access: <https://boardgamegeek.com/applications>. |
| `BACKUP_HOUR` / `BACKUP_KEEP` | `3` / `14` | Daily SQLite backup time (local) and how many to keep. `BACKUP_KEEP=0` disables the schedule. |
| `DEV_MODE` | `false` | Bypasses SSO with a user picker. Local testing only; never expose it. |

## Who can do what

| | Any signed-in player | Admin |
|---|---|---|
| Log plays, add games and guests | ✓ | ✓ |
| Edit or delete a play | If they logged it or played in it | Any |
| Archive or delete games, sync, merge guests | | ✓ |

## Backups

With SQLite, the app writes a gzipped, integrity-checked snapshot to `data/backups/` every night. It keeps the newest `BACKUP_KEEP`, and you can trigger or download one from **Admin → Backups**.

Back up the whole `./data/` folder off the box: photos and covers live there too. To restore, stop the container and run `gunzip -c data/backups/boardgames-<stamp>.db.gz > data/boardgames.db`. Then delete any `boardgames.db-wal` and `-shm` files and start the container again.

With Postgres, schedule `pg_dump` yourself. The CSV and JSON exports are a human-readable backup either way.

## Ratings

Ratings use [OpenSkill](https://github.com/vivekjoshy/openskill.py) (Plackett–Luce model), recomputed from full history, so editing an old play fixes everything after it.

- **Displayed number:** `1000 + 40 × (μ − 3σ)`. Everyone starts at 1000.
- **Provisional:** a "?" marks fewer than 5 rated plays.
- **What counts:** co-op plays and plays where everyone tied are not rated.
- **Teams:** players with the same team name move together.

## Importing

Imports are under **Admin → Import plays**:

- **CSV:** this app's own export, or a spreadsheet with `played_at` (or `date`), `game` and `player` columns, plus optional `score`, `winner`, `team`, `location`, `duration_min`, `notes` and `play_id`.
- **BGG plays XML file**, or a **BGG username** (needs `BGG_TOKEN`).

You always get a preview before anything is written. Players are matched by username, then by name, and unknown people become guests. Duplicates (same day, game and players) are skipped, so re-running an import is safe.

## BGG collection sync

Go to **Admin → Sync BGG collection** and enter your BGG username (needs `BGG_TOKEN`). The sync only looks at board games marked **Own**; expansions are skipped. The preview shows exactly what will happen:

- **New games** are added as "highest score wins". Change co-op or win/loss games afterwards.
- **Existing games** (matched by BGG ID, then name) only get *blank* fields filled in. Your edits are kept.
- **Games that left the collection** can be marked "not on our shelf" (history kept), archived, or left alone.
- **Covers** download in the background.

The first fetch can take up to a minute while BGG prepares the collection. If it times out, run it again; BGG caches the result. Each game also has an **On our shelf** checkbox you can set by hand.

## Installing on your phone

Open the site over HTTPS, then use **Share → Add to Home Screen** (iOS) or **Install app** (Android/Chrome). "Log a play" is cached for offline use. If you lose signal mid-game, the play is kept as a draft on the phone. Open "Log a play" again once you're back online and tap Save.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
SECRET_KEY=$(openssl rand -hex 32) DEV_MODE=true DATABASE_URL=sqlite:///./dev.db \
  .venv/bin/uvicorn app.main:app --reload
```

Schema changes go through Alembic. Migrations run automatically at startup.

```bash
.venv/bin/alembic revision --autogenerate -m "describe change"
```

**Stack:** FastAPI, SQLAlchemy 2, Alembic, Authlib, OpenSkill, Pillow (+ pillow-heif), Jinja templates, a small amount of vanilla JS, and Chart.js (vendored in `app/static/vendor`).
