# Running locally and in production

The easiest local commands are **`run.bat`** on Windows and **`sh run.sh`** on Linux. They prepare the Python environment, preserve your `.env`, and start the same code on either OS. Python 3.11+ is required; 3.12 is recommended.

## Service modes

- Default: bot and website together when a bot token is configured; website preview when it is blank.
- `--web-only`: website without connecting the bot. Real server settings are unavailable because the bot cannot verify its server membership; the public `/preview` remains usable.
- `--bot-only`: original Gateway bot, with no web listener.
- `--setup`: launcher-only option to prepare dependencies and create `.env`, then exit.
- `--test`: launcher-only option to run the complete automated suite.

Once dependencies are installed, the direct command is `.venv\Scripts\python.exe -m espresso` on Windows or `.venv/bin/python -m espresso` on Linux. Ctrl+C stops a local run. The combined web service also handles SIGTERM with orderly web, Discord, and database cleanup.

First-run installation needs internet access. Runtime needs access to Discord's API/Gateway. Google Fonts is optional for appearance; system fonts are used if it cannot load. A Node.js installation or frontend compilation is unnecessary.

## Public hosting at espresso.harperurias.com

For the existing always-on origin behind Cloudflare Tunnel, follow [PUBLIC_SERVER.md](PUBLIC_SERVER.md). It covers the distinction between the origin computer and a separate development PC, production callbacks, and preview-mode troubleshooting.

Use a continuously running Python host with persistent storage. Keep a **single application process**, with the bot and panel sharing the same `Store` object and event loop. Do not deploy independent bot/web SQLite writers, use multiple replicas, or run behind a request-only static site host.

1. Prepare the project on the host with `sh run.sh --setup` (Linux) or `run.bat --setup` (Windows).
2. Configure secrets privately, preferably through the hosting provider's secret manager. Use encrypted host storage and protect the database/backups. Set `DATABASE_PATH` to a durable disk location.
3. Set `WEB_BASE_URL=https://espresso.harperurias.com`. Bind to `127.0.0.1:8080` behind a trusted HTTPS reverse proxy; if using a container platform, bind to its internal interface and keep that upstream port private.
4. Configure the proxy to forward to the application, terminate TLS, preserve the original origin, and avoid logging callback query strings, cookies, or authorization headers. The app disables its own HTTP access log and does not trust forwarded headers for URL/cookie decisions.
5. Attach the `espresso` custom subdomain at your host. Add only the DNS record the host supplies (A/AAAA or CNAME); leave `harperurias.com`'s existing root-site records alone.
6. Register the production OAuth callback from [DISCORD_PORTAL.md](DISCORD_PORTAL.md), finalize the policy pages, and test with non-admin Discord accounts before opening the service.

No DNS, remote hosting, or public release is performed by the repository's launchers.

## Sessions and security boundaries

OAuth state is single-use, browser-bound, and expires after five minutes. Sessions are opaque, host-only, HttpOnly cookies with SameSite=Lax; cookies become Secure when the configured origin uses HTTPS. Sessions expire after at most eight hours (or token expiry), live only in memory, and are cleared at process restart/logout. Refresh tokens are not stored. The app requests fresh Discord guild permissions on every server settings read and write.

Settings writes require a same-origin request plus a session CSRF token. Role/channel IDs are resolved in the selected server; numeric fields use the same validation as the bot. Saves use a version number, so edits from another tab or slash command return a conflict rather than silently replacing newer values. Audit metadata is committed alongside the settings. Discord snowflakes cross the browser API as strings.

OAuth login attempts are rate-limited per connection peer, with bounded in-memory session/state storage. For public hosting, add appropriate request limits at the trusted proxy too. When behind a proxy, peers may share an address, so tune those limits and test login traffic before release. Do not blindly trust client-supplied forwarding headers.

## Operations

Configure your host's process supervisor to restart on failures and shut down normally. Start the virtual environment's Python directly for unattended runs; package updates should be an explicit deployment step, not an automatic service boot action.

Back up SQLite consistently by stopping the process before copying the main database, or use SQLite's online backup facilities. Copying only the main file during WAL activity is insufficient. Backups and process logs need restricted access, rotation, and a retention/deletion process. These hosting practices must match the public Privacy Policy.

The schema adds settings versions and panel audit records through idempotent `CREATE TABLE IF NOT EXISTS` statements; existing bot settings/warnings are preserved. Back up your database before an upgrade. A future multi-process deployment should move to a shared transactional database with explicit migrations, not depend on the current Python lock.

## Current verification limits

Automated Python tests run on Windows with mocked Discord calls; a headless browser checks desktop/mobile layouts and the settings form. The shell launcher uses portable `sh` and Python's cross-platform environment support, but a real Linux host and real OAuth credentials still need end-to-end verification. No bot token or OAuth client secret is included.
