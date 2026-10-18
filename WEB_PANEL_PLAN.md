# Espresso Bot panel — current foundation and next steps

**Planned address:** `https://espresso.harperurias.com`  
**Current status:** working local implementation; not publicly hosted.

## Implemented

- Home page that follows Harper's existing light lavender/pink palette, serif headings, rounded controls, and dark navigation.
- Responsive settings panel and a clearly labeled public `/preview` with no server writes.
- Discord OAuth authorization-code flow with `identify` and `guilds`, one-use browser-bound state, expiring server-side sessions, and logout.
- Server selection limited to servers the signed-in user owns or can manage.
- Real settings reads/saves for filters, spam limits, blocked phrases, escalation, exempt roles, and log channel.
- Independent 1–5 racism, misogyny, and anti-trans hate coverage controls, per-category actions, additional terms, and exact-match exceptions.
- Reaction-role configuration and raw Discord reaction handlers, including optional removal of recorded grants, persistent ownership records, and checks against privileged roles.
- Fresh Discord permission checks per read/write, CSRF/origin validation, string Discord IDs in browser JSON, server-owned role/channel checks, and conflict detection for stale forms.
- Atomic settings/audit writes and immediate configuration use by the running bot.
- Terms and Privacy starter pages, with guidance for the portal fields in [DISCORD_PORTAL.md](docs/DISCORD_PORTAL.md).
- Windows/Linux launchers, local `.env` loading with environment precedence, and combined bot/site startup.

## Architecture

The app uses aiohttp and discord.py on one asyncio event loop. The panel and Gateway bot share a `Store` instance, including its transaction lock. Public pages are static HTML/CSS/JS served by that same application, with no frontend build step. SQLite persists settings and warnings; OAuth access tokens live only in memory. New settings version/audit tables are created without replacing existing data.

`/api/session`, `/api/servers`, and `/api/servers/{guild_id}/settings` are implemented. The settings route supports GET and versioned PATCH. State-changing requests require a session CSRF header and the configured origin. Public endpoints never return bot tokens, client secrets, OAuth access tokens, or moderation histories.

## Next steps before public release

1. Supply your Discord application credentials and test the full local OAuth flow with the exact callback URI.
2. Verify installation, permissions, save conflicts, role changes, and failed Discord calls in a real test server.
3. Choose the persistent Python host and HTTPS reverse proxy; configure secrets, encrypted host storage, backups, retention, monitoring, and public request limits.
4. Finalize the Terms/Privacy text against that actual deployment and data handling.
5. Attach `espresso.harperurias.com`, register its production callback, and set the portal's policy URLs after they are reachable.

## Possible later features

Channel exemptions, domain allowlists, per-rule actions, warning retention controls, fuller status diagnostics, and a staff moderation history UI. These are not implemented. A moderation action UI would need the separate Moderate Members permission and target hierarchy checks already used by slash commands.

Before adding independent services or multiple replicas, migrate to a shared transactional database and controlled Discord action queue. The current in-process Python lock is intentionally designed for one application process.
