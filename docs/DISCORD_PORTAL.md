# Discord Developer Portal setup

Create one application for **Espresso Bot** in the [Discord Developer Portal](https://discord.com/developers/applications). Use its bot token for the Gateway connection and its OAuth2 client ID/secret for website sign-in.

## General Information — the fields from your screenshot

| Portal field | Value for this version |
| --- | --- |
| Interactions Endpoint URL | **Leave blank.** Commands arrive through discord.py's Gateway connection. The website has no HTTP interactions receiver. |
| Linked Roles Verification URL | **Leave blank.** Exempt moderator roles are ordinary server settings, not Discord Linked Roles. No role verification workflow is implemented. |
| Terms of Service URL | `https://espresso.harperurias.com/terms-of-service` — after hosting is live and the initial terms have been reviewed |
| Privacy Policy URL | `https://espresso.harperurias.com/privacy-policy` — after hosting is live and host/retention details have been finalized |

Do not paste the home page, settings API, or OAuth callback into the Interactions Endpoint field. Discord's [interaction delivery methods](https://docs.discord.com/developers/interactions/receiving-and-responding) are mutually exclusive. This app intentionally receives commands through the Gateway. A future HTTP interactions receiver would require Discord signature verification and a different delivery setup.

The [Linked Roles workflow](https://docs.discord.com/developers/tutorials/configuring-app-metadata-for-linked-roles) uses role-connection metadata and extra scopes. It is unrelated to signing in to this panel and is outside this version's scope.

The included policy pages describe current behavior as an initial version. Finalize operator/contact details, actual hosting logs, encrypted host storage, backup/retention practices, and any jurisdiction-specific obligations before public launch. Discord's [Developer Terms](https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service) and [Developer Policy](https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy) apply; the pages are starter content, not a compliance certification.

## OAuth2 → Redirects

Register the exact callback addresses you will use:

- Local: **`http://localhost:8080/auth/discord/callback`**
- Production: **`https://espresso.harperurias.com/auth/discord/callback`**

Keep **Public Client off**. This app performs authorization-code exchange on its server using a confidential client secret; it is not a public desktop/mobile OAuth client. Bot Token and OAuth Client Secret are different values. Save both in their separate `.env` entries, without a `Bot ` prefix. Never paste credentials into chat or screenshots.

`WEB_BASE_URL` must match the browser's origin, including scheme and port. A callback for localhost is different from one for `127.0.0.1`; open the site using the configured URL. If you change port 8080, update the base URL and the registered callback too. No wildcard or arbitrary return URL is used.

Copy the **Application ID** into `DISCORD_CLIENT_ID` and the **OAuth2 Client Secret** into `DISCORD_CLIENT_SECRET` in `.env` or the host's secrets configuration. Never use the bot token as the client secret. The app requests only **identify** and **guilds** for panel sign-in; it does not request email, join servers automatically, or request Linked Roles access.

## Installation and Bot settings

With website OAuth configured, **Add Espresso** requests `bot applications.commands identify guilds` in a single browser authorization and returns to the authenticated dashboard. With only the application ID configured, installation falls back to a basic bot/commands invite. The website's normal Sign in button still requests only `identify guilds`. The selected server is only a navigation hint; the panel verifies fresh management permissions before accessing its settings. New servers receive the [setup guide](SERVER_SETUP.md).

Enable Guild Install with **bot** and **applications.commands** scopes. Enable **Message Content Intent** for the bot. Server Members Intent is required only when optional member screening is enabled on the host with ENABLE_MEMBER_SCREENING_EVENTS=true. Presence Intent is unused. See MEMBER_SCREENING.md. The `/invite` page constructs the installation link from the application ID, requesting View Channel, Send Messages, Send Messages in Threads, Read Message History, Manage Messages, Embed Links, Moderate Members, Add Reactions, and Manage Roles. Manage Roles and Add Reactions support the opt-in reaction-role feature; existing installations need those permissions enabled before using it.

Place the bot's role above members it should timeout. Restrict the chosen log channel to staff. Configuration commands require Manage Server; manual moderation commands require Moderate Members. The panel checks the user's current ownership/Manage Server/Administrator permission for every settings read and write, not just at login.

## End-to-end check with your credentials

1. Set `.env`, register the local callback, and restart with `run.bat` or `sh run.sh`.
2. Open the configured localhost URL and choose Sign in with Discord.
3. Confirm only servers you manage appear. Add the bot if needed, then refresh.
4. Change one harmless setting and save. Verify `/espresso settings` shows the update.
5. Open two tabs; save in one, then save a stale form in the other. The second should be rejected with a reload message. Slash-command changes also invalidate older forms.
6. Revoke Manage Server from a test user after login; settings reads and writes must fail.
7. Test logout and a cancelled sign-in. Verify regular members cannot configure other servers by changing the URL.

Real OAuth sign-in cannot be validated without your application credentials and registered callback. Automated tests use mocked Discord responses and do not connect real accounts.
