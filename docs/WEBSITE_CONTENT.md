# Keep the website in step with Espresso

The home page's **On the menu** section is rendered from `espresso/public/features.json`. It works without browser JavaScript. Add a feature to this file rather than building another separate set of cards.

Each feature has a unique lowercase `id`, `status`, `category`, `title`, and `description`. Available features may also have a `link` with a local `href` and `label`.

Statuses:

- `available`: implemented and ready to use in this checkout. Its setup or detail link appears.
- `in_development`: being built. The card is visibly labeled **In development** and has no active setup link.
- `planned`: announced for later. The card says **Coming soon** and has no active setup link.

Member screening is currently **in development** and is not part of this checkout. Keep its wording general until its implementation and setup are available here. When ready, update the description to match its actual behavior, change its status to `available`, and add a link to its setup or guide. Do not advertise screening as part of the current bot bio until it is available.

For each new feature:

1. Confirm that the implementation and setup work.
2. Add or update its catalog entry, including the correct status.
3. Add detailed website guidance when the feature needs more explanation.
4. Update the public bio if it changes the bot's main capabilities; keep the website link and stay within Discord's 400-character description limit.
5. Check the page on desktop and mobile, and check its links.
6. Deploy the source update to the always-on origin and restart Espresso, as described in `docs/PUBLIC_SERVER.md`. Editing this checkout does not update a separate server automatically.

The catalog is read on each home-page request. Updating it on the running origin does not require a frontend build. Backend changes still require a process restart.

The public bio copy is kept in `assets/branding/discord-bio.txt`.
