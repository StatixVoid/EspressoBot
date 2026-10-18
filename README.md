# ☕ Espresso Bot

A small, unofficial Sabrina Carpenter-inspired Discord moderation bot with a matching website and server settings panel. Written in Python with discord.py, aiohttp, and asynchronous SQLite. The bot and panel share one process and the same validated settings.

Planned public address: **https://espresso.harperurias.com**. The local site includes a home page, Discord sign-in, server selection, settings editor, Terms, and Privacy. No public hosting or DNS changes have been made. See [Discord Portal setup](docs/DISCORD_PORTAL.md), [hosting](docs/HOSTING.md), and [panel status](WEB_PANEL_PLAN.md).

## Quick start — Windows and Linux

For server owners, the website now guides first-run setup: pick a staff log channel (or explicitly keep logging off), choose starter filters, and decide whether to enable automatic moderation. The full panel follows for detailed filters and reaction roles. The guide is saved separately for each server. See [server setup and chat commands](docs/SERVER_SETUP.md).

For this project's existing always-on server and Cloudflare Tunnel, see [public server setup](docs/PUBLIC_SERVER.md). GitHub stores the source; the origin computer must run the full Python app with its own private configuration. `/espresso setup` and mention-based `@Espresso setup` / `@Espresso help` provide the same server-specific panel link and configuration command guide.

Install **Python 3.11 or newer** (3.12 recommended). On Windows, enable the Python launcher/PATH during installation. On Debian/Ubuntu, install `python3-venv` if environment creation reports it missing.

**Windows:** open the project folder and double-click **`run.bat`**, or run `.\run.bat` in PowerShell.

**Linux:** open a terminal in the project folder and run **`sh run.sh`**. You can also use `chmod +x run.sh` once, then `./run.sh`.

The launcher creates `.venv`, installs the pinned libraries, and copies `.env.example` to `.env` once. It never overwrites your configuration. Later launches skip installation unless requirements changed. First launch requires internet access.

Open **http://localhost:8080**. With an empty bot token, the site runs in preview mode: the home page, interactive examples, panel preview, Terms, and Privacy work without credentials. With `DISCORD_TOKEN` configured, the same launcher starts the bot and site together. Stop with **Ctrl+C**.

The website uses the clean coffee-cup logo, a pink/purple connected-particle background inspired by harperurias.com, and an interactive house-blend receipt for exploring example moderation settings. The background can be paused and respects reduced-motion preferences.

The home page reads **`GET /api/stats`** every 30 seconds while visible. Server totals come directly from the running bot's current Discord server cache, so joins and removals appear on the next refresh. The endpoint publishes only the aggregate count, connection status, gateway heartbeat latency, and check time; server names, IDs, member data, and credentials are excluded. Preview mode, a disconnected bot, or a failed request shows a clear state with unavailable values instead of a made-up count. Run the website and bot together for live stats; `--web-only` always shows preview status.

Edit `.env` privately when ready:

```dotenv
DISCORD_TOKEN=your_bot_token
DISCORD_CLIENT_ID=your_application_id
DISCORD_CLIENT_SECRET=your_oauth_client_secret
WEB_BASE_URL=http://localhost:8080
```

Register **`http://localhost:8080/auth/discord/callback`** under Discord → OAuth2 → Redirects. Then restart and sign in. Real server configuration requires the bot online and installed in a server you can manage. The OAuth client secret and bot token are different secrets; neither belongs in browser code.

| Task | Windows | Linux |
| --- | --- | --- |
| Website + bot (or preview without token) | `run.bat` | `sh run.sh` |
| Website preview only | `run.bat --web-only` | `sh run.sh --web-only` |
| Bot only | `run.bat --bot-only` | `sh run.sh --bot-only` |
| Install/configure without starting | `run.bat --setup` | `sh run.sh --setup` |
| Run automated tests | `run.bat --test` | `sh run.sh --test` |

These scripts work from their own directory even when launched from elsewhere. For an unattended production service, prepare `.venv` first and run its Python directly rather than installing packages at every service boot.

## What is included

- Separate persistent settings for each server, controlled through slash commands.
- Spam detection across a member's channels, link/invite filters, and a literal blocked-word/phrase list.
- Separate racism, misogyny, and anti-trans hate filters with cumulative coverage levels 1–5, per-category actions, extra terms, and exceptions.
- Opt-in reaction roles on existing messages, with protected-role/hierarchy checks and optional removal when members unreact.
- New messages and content edits, including messages outside the local cache.
- Message deletion, persistent warnings, configurable warning escalation, and native Discord timeouts.
- Manual warning, warning history, warning clearing, timeout, and timeout removal.
- Role exemptions for automatic moderation, staff permission checks, and role hierarchy checks.
- Private command responses, optional staff-channel logs, and warning DMs when available.
- Private `.env`/environment configuration and automatic Windows/Linux setup. No token is required to preview the site or run automated tests.

## 1. Create the Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications), create an application named **Espresso**, and open its **Bot** settings.
2. Create/reset the bot token and store it privately. It is a password; never put it in source code, screenshots, a dashboard frontend, or a public repository. If exposed, reset it immediately.
3. Enable **Message Content Intent** under privileged gateway intents. This is required to inspect normal chat messages. Server Members and Presence intents are not used by this MVP. Discord may require intent approval as your bot grows; follow the portal's requirements.
4. Configure **Guild Install**. Use the installation/OAuth2 URL generator with scopes **bot** and **applications.commands**. Install to a test server first.
5. Request these bot permissions: **View Channels**, **Read Message History**, **Manage Messages**, **Send Messages**, **Embed Links**, **Moderate Members**, and **Send Messages in Threads** if moderating accessible threads. For reaction roles, also grant **Manage Roles** and **Add Reactions**. Administrator is unnecessary.
6. In the server's role settings, move Espresso's highest role above members it should timeout. Check channel overwrites as well as server permissions.
7. Create a private staff text channel such as `#espresso-logs`. Restrict access using Discord's channel permissions; the bot does not make the channel private for you.

The library's [intent guide](https://discordpy.readthedocs.io/en/stable/intents.html) explains Message Content access. Discord documents [permissions](https://docs.discord.com/developers/topics/permissions) and [timeouts](https://docs.discord.com/developers/resources/guild#modify-guild-member).

## 2. Install and run

The launchers above are recommended. For manual installation, use **Python 3.11+** and one process. Open a terminal in the `EspressoBot` folder containing this README and create a virtual environment.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Read the token without echoing it or storing it in command history.
$espressoSecret = Read-Host 'Discord bot token' -AsSecureString
$env:DISCORD_TOKEN = [System.Net.NetworkCredential]::new('', $espressoSecret).Password
Remove-Variable espressoSecret

# Optional: copy your test server ID with Discord Developer Mode enabled.
$env:DEV_GUILD_ID = 'YOUR_TEST_SERVER_ID'
$env:DATABASE_PATH = 'data/espresso.db'
.\.venv\Scripts\python.exe -m espresso
```

Replace `YOUR_TEST_SERVER_ID` with the numeric server ID, or omit that line to register commands globally. After stopping the bot, `Remove-Item Env:DISCORD_TOKEN` removes the token from this shell. The program does not print it.

### macOS / Linux (Bash)

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
read -rsp 'Discord bot token: ' DISCORD_TOKEN
echo
export DISCORD_TOKEN
export DEV_GUILD_ID='YOUR_TEST_SERVER_ID'
export DATABASE_PATH='data/espresso.db'
.venv/bin/python -m espresso
```

Use `unset DISCORD_TOKEN` after stopping. The token lives in the process environment while running; restrict access to the host account and use your hosting provider's secret manager for production.

The app loads only the project root's **`.env`**, using UTF-8 (including Windows BOMs). Existing environment variables take precedence, so hosting secret managers remain supported. No real credentials are included, and `.env` is ignored by Git. On Linux, the launcher creates new `.env` files with owner-only permissions; restrict the folder on Windows too.

| Variable | Required | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | For bot | Bot token; blank starts website preview only |
| `DEV_GUILD_ID` | No | Register slash commands only in this test server; blank/unset registers globally |
| `DATABASE_PATH` | No | SQLite file, default `data/espresso.db`, relative to working directory |
| `DISCORD_CLIENT_ID` | For sign-in/invites | Numeric application ID from the Developer Portal |
| `DISCORD_CLIENT_SECRET` | For sign-in | OAuth2 client secret, stored only on the server |
| `WEB_BASE_URL` | No | Browser origin; default `http://localhost:8080`; remote origins must use HTTPS |
| `WEB_HOST` | No | Bind address; default `127.0.0.1`; use a reverse proxy for public HTTPS |
| `WEB_PORT` | No | Local web port, default `8080`; change base URL/callback to match |

Development registration is immediate for the chosen server. When moving to global registration, remove the development server's command copies to avoid duplicate menus. From an environment with the libraries installed, run this **once**, with `DISCORD_TOKEN` and `DEV_GUILD_ID` set:

```bash
python -m espresso --clear-dev-commands
```

Use your virtual environment's Python. Then unset `DEV_GUILD_ID` (`Remove-Item Env:DEV_GUILD_ID` in PowerShell or `unset DEV_GUILD_ID` in Bash) and start the bot normally to register globally.

Alternatively, leave `DEV_GUILD_ID` set while testing and defer global registration until you need additional servers. Global changes may take time to appear in clients. Commands are synchronized in `setup_hook`, not on every reconnect.

## 3. Configure your server

Automatic moderation starts **off**. Server owners/admins or members with **Manage Server** can use `/espresso` commands. Staff commands under `/mod` require **Moderate Members**. Runtime checks enforce these requirements even if command visibility is changed in Discord.

Start with:

```text
/espresso logs channel:#espresso-logs
/espresso exempt action:add role:@Trusted
/espresso word action:add phrase:testword
/espresso filters block_links:False block_invites:True spam_enabled:True
/espresso spam limit:6 window_seconds:8
/espresso escalation threshold:3 window_hours:24 timeout_minutes:10
/espresso settings
/espresso enable enabled:True
```

Discord presents each option as a form field; select a real channel and role. Use a harmless test phrase first, then replace it with your own list. The settings command includes a downloadable JSON snapshot with the complete lists for review; it is not an import interface.

| Command | Purpose |
| --- | --- |
| `/espresso settings` | View settings and download all lists |
| `/espresso enable enabled` | Pause/resume automatic moderation; manual commands remain available |
| `/espresso filters [block_links] [block_invites] [spam_enabled]` | Change only supplied switches |
| `/espresso spam limit window_seconds` | Trigger on the Nth message in a rolling window; count 2–50, window 2–60s |
| `/espresso hate category enabled level action` | Configure racism, misogyny, or trans_hate coverage (1–5) and delete/warn/timeout action |
| `/espresso escalation threshold window_hours timeout_minutes` | Threshold 0–20, active window 1–720h, timeout 1–40,320 minutes; 0 threshold disables escalation |
| `/espresso word action phrase` | Add/remove a literal phrase, up to 100 entries of 80 characters |
| `/espresso exempt action role` | Add/remove up to 100 automatic-filter role exemptions |
| `/espresso logs [channel] [disabled]` | Choose a log channel and send a test log, or use `disabled:True` |
| `/mod warn member reason` | Record one warning; may apply configured escalation |
| `/mod warnings member` | Show latest 10 warnings, including those outside the active window |
| `/mod clearwarnings member` | Permanently delete this member's warning history; existing timeout stays |
| `/mod timeout member minutes reason` | Apply a timeout; preserve an existing longer timeout |
| `/mod untimeout member reason` | Remove a timeout explicitly |

## Exact moderation behavior

For the detailed new controls and reaction-role setup, see [FILTERS_AND_ROLES.md](docs/FILTERS_AND_ROLES.md). The identity-based filters start off and use limited English word/phrase rules; they do not infer intent or automatically understand context. Reaction roles also start off and are independent of moderation.

- **Defaults:** automatic moderation off; spam detection on at 6 messages/8s; invite blocking on; link blocking off; empty word and exemption lists; no log channel. Escalation is 3 warnings in 24h → 10-minute timeout.
- **Spam:** counts new messages per member across the server, including messages with attachments. It triggers at the configured limit and continues while the rolling count stays at or above that limit. Edits are filtered for text but do not count as new spam hits. Restarting resets spam counters. At most 10,000 active member buckets are held; oldest buckets are evicted under heavy load.
- **Content:** invite detection covers `discord.gg`, `discord.com/invite`, and `discordapp.com/invite`. Link blocking detects HTTP(S), `www`, and ordinary bare domains; it has no allowlist in this MVP. Words/phrases are case-insensitive literal matches with word boundaries. Unicode compatibility normalization and invisible format-character removal catch some simple evasions. Spaces within a phrase must match literally.
- **Limits:** this is deterministic text filtering. Obfuscated URLs/words, homoglyphs, redirect destinations, attachment contents, images, audio, and text embedded in uploads are not scanned. Domain detection can produce false positives. Start with conservative rules and test them on your community's text.
- **Exemptions:** bots, webhooks, system messages, the owner, administrators, and configured exempt roles skip automatic filters. Staff with Manage Server/Moderate Members are **not** automatically exempt unless they have Administrator or an exempt role. Role exemptions do not block manual staff moderation.
- **Warnings:** each violating message is deleted where possible. A member receives at most one automatic warning every 30 seconds, across all filter types; this cooldown persists in SQLite. Multiple edits of the same warned message cannot create extra warnings. Manual warnings use their interaction ID for deduplication and bypass the automatic cooldown.
- **Escalation:** every newly recorded warning, including manual warnings, counts in the rolling active window. At or above the threshold, the bot attempts the configured timeout. Older warnings remain visible but no longer count. Changing settings does not apply penalties retroactively. Clearing warnings resets their cooldown/count history too.
- **Hierarchy:** manual warnings, warning clearing, and timeout commands require the actor's highest role above the target, unless the actor is the owner, and Espresso's role above the target. Bots, the owner, administrators, and yourself cannot be manual targets. Automatic message deletion/warnings can still work for higher roles, but a timeout will be skipped if the bot cannot act on them.
- **Failure reporting:** a failed deletion is logged and the warning/escalation still proceeds. A message already removed by someone else is ignored. Timeout outcomes are included in the warning result. Closed DMs do not block enforcement. Missing log permissions produce local metadata warnings; they do not make the moderation action fail.
- **Scope:** monitors events received while online in channels/threads it can access. It does not backfill offline messages or join every private thread. Discord enforces a maximum timeout of 28 days. Longer existing timeouts are preserved; `/mod untimeout` is the explicit removal path.

## Tests and a short server check

```bash
python -m unittest discover -s tests -v
```

Use the virtual environment's Python if it is not activated. Automated tests cover filtering, isolated server settings, durable warnings, concurrent writes, deduplication/cooldown, role/permission checks, escalation, longer timeout preservation, uncached edits, and failed Discord actions. All Discord calls in these tests are mocked.

Before real use, check a private test server with a **regular non-admin account**:

1. Confirm disabled moderation leaves messages alone. Set the private log channel and enable moderation.
2. Post `testword`, edit an older clean message to `testword`, and post a test invite. Confirm deletion, one warning/30s, and log outcomes.
3. Send six messages within eight seconds, then stop and wait for the window to expire.
4. Apply/remove an exempt role. Verify that it bypasses automatic filters and does not grant commands.
5. Try the staff commands as a regular member and confirm refusal. Issue manual warnings as authorized staff until escalation occurs, then test `/mod untimeout` and `/mod clearwarnings`.
6. Restart the bot and confirm settings/warnings survived. Test missing Manage Messages and missing Moderate Members separately; verify failure logs and restore permissions.

No live Discord login or server actions were performed during project creation. Supply your own token and complete this check before enabling in a community.

## Hosting and data

Use a host that supports a continuously running Python application and outbound HTTPS/WebSocket connections. The bot alone does not need a public inbound port or DNS record; the website needs an HTTPS reverse proxy to its local web port. Run **one instance per token/database**; request-only serverless hosting and multiple replicas are not suitable for this MVP. See [hosting details](docs/HOSTING.md).

- Install dependencies in an isolated environment, inject the token through secrets, and run `python -m espresso` with the project directory as working directory.
- Set `DATABASE_PATH` to a persistent writable disk path. Ephemeral disks lose warnings/settings after redeploy. Use the host's process supervisor with restart-on-failure and normal graceful shutdown.
- For a consistent simple backup, stop the bot and copy `data/espresso.db`; then restart. Do not copy only the main file while it is running in WAL mode. Protect backups and the database with host filesystem permissions.
- SQLite stores server settings, user/moderator IDs, timestamps, warning reasons, message/interaction IDs, settings versions, and panel settings audit metadata (actor, time, changed keys). Reasons are limited to 300 characters; automatic warnings store only the rule name. Raw message bodies and attachments are not stored in the database.
- Staff-channel and process logs contain action metadata and outcome, not message bodies or manual reasons. Reasons remain in SQLite, private warning history responses, warning DMs, and timeout audit reasons. Configure host log rotation/retention and restrict database access.
- Warnings remain until staff clear them. When the running bot receives a server-removal event, its settings and warnings are deleted; offline removals require an operator cleanup. Backups and Discord log messages are not deleted automatically.

Code layout: `rules.py` holds validated settings and pure detection, `store.py` holds persistence and versioned saves, `bot.py` handles Discord, `web.py` handles OAuth/API routes, `config.py` loads runtime settings, `public/` holds the pages, and `__main__.py` runs the services. No Node.js build step is needed.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Bot fails to log in | Reset/update the bot token; do not use the application client secret |
| Privileged-intent error or filters miss text | Enable Message Content Intent in the portal and restart |
| Slash commands missing | Guild Install + applications.commands scope, correct DEV_GUILD_ID, staff permissions, client refresh |
| Messages stay visible | `/espresso enable`, channel access, Manage Messages, and role exemptions |
| Edits not detected | Read Message History and access to that channel/thread |
| No timeout | Active warning threshold, Moderate Members, bot/actor role position, protected target |
| No staff logs | Set the channel; allow View Channel, Send Messages, Embed Links; inspect process metadata logs |
| Settings vanish after deploy | Persist DATABASE_PATH on disk and reuse the same path |

Branding is fan-inspired and unofficial. The project includes no artist photographs, logos, or song lyrics.
