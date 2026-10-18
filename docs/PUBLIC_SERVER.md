# Run the public panel on the existing always-on server

GitHub stores the source. The always-on computer runs the Python bot, website, and SQLite database. A GitHub Pages deployment cannot replace that running application.

The existing Cloudflare Tunnel can route **espresso.harperurias.com** to **http://localhost:8080** on the computer running its connector. `localhost` means that connector's computer, not a developer's separate PC. Keep the other published application routes intact.

## Configure the origin computer

Update Espresso's source on the always-on computer using your normal GitHub workflow. Keep that computer's private `.env`, database, and virtual environment. Do not commit or send secrets through chat. If existing settings or warnings need to be moved from another computer, stop its bot and take a consistent SQLite backup before transferring them privately.

In Espresso's `.env` on the origin computer, set:

```dotenv
DISCORD_CLIENT_ID=1561286837186592902
WEB_BASE_URL=https://espresso.harperurias.com
WEB_HOST=127.0.0.1
WEB_PORT=8080
DATABASE_PATH=data/espresso.db
```

Privately fill in that file's `DISCORD_TOKEN` and `DISCORD_CLIENT_SECRET` with the corresponding values from Discord. The public application ID and public key cannot replace either secret. Environment variables override `.env`; remove an old `WEB_BASE_URL` environment override if necessary.

In Discord's OAuth2 Redirects, register both exact callbacks and save:

```text
http://localhost:8080/auth/discord/callback
https://espresso.harperurias.com/auth/discord/callback
```

Keep Public Client off and Message Content Intent on. Leave Interactions Endpoint URL and Linked Roles Verification URL blank for this Gateway/reaction-role implementation.

## Start one full application

Stop the origin's preview process. Run **run.bat** on Windows or **sh run.sh** on Linux, with no `--web-only` argument. Stop a development bot using the same token before starting the public bot; two active processes can receive the same moderation events. The website and bot must run together in one process with persistent storage. A blank token starts preview mode automatically.

The tunnel's service URL stays `http://localhost:8080`, even though the public `WEB_BASE_URL` uses HTTPS. TLS terminates at Cloudflare. Do not expose the origin port directly. Review the actual host's startup/restart arrangement so both Espresso and the tunnel survive reboot; these launchers do not install a Windows service or startup task.

After switching to the HTTPS base URL, use the public site in your browser. Its sign-in cookies are Secure and cannot be tested through plain localhost HTTP. A separate development copy may retain the localhost base URL.

## Verify

- Visit `https://espresso.harperurias.com/api/stats`. `status` must be `online`; `preview` means the origin is running without its bot. A disconnected bot reports `offline`.
- Choose Sign in with Discord from the public home page. The authorization page's `redirect_uri` must point to **https://espresso.harperurias.com/auth/discord/callback**. If it contains localhost, the origin still has the local base URL or an old environment override.
- After authorization, select your server, choose the private log channel in the guide, and save. Verify `/espresso settings` shows the same choices.
- Test moderation and reaction roles with a regular non-admin test account in a test channel. Do not test abusive language in a community channel.

Do not cache `/auth/*`, `/api/*`, or `/dashboard` at Cloudflare. These responses already send `Cache-Control: no-store`. Avoid logging callback query strings, cookies, and authorization headers at any proxy. Finalize the policy pages for actual hosting before inviting the public.

For an existing tunnel, use its published application route. For a new tunnel, follow Cloudflare's [dashboard tunnel guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/). Keep connector tokens private and use the provider's instructions to install its service. No tunnel token belongs in this repository.
