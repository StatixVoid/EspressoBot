# Premium Insights pilot

The first premium feature is an aggregate moderation report in the dashboard's
Overview and `/premium insights days:7` or `days:30`. `/premium status` checks
access. `/myid` privately shows the invoking member's user and server IDs.

Pilot defaults:

| Scope | Discord ID |
| --- | --- |
| Harper's server | `1432165456684519587` |
| Harper's account | `926272518275362827` |

Server allowlisting unlocks the feature for that server's authorized staff.
User allowlisting unlocks it for that account in servers where it already has
Manage Server, Moderate Members, or Administrator. Neither allowlist grants
server membership, staff privileges, moderation exemptions, or dashboard login
bypasses. Every API request rechecks current Discord server permissions.

`PREMIUM_ENABLED=false` disables pilot access. `PREMIUM_GUILD_IDS` and
`PREMIUM_USER_IDS` accept comma-separated numeric IDs. An unset variable uses
the project defaults above; an explicitly empty variable removes that list.
Changes require a process restart. Environment values override `.env`.
Configuration errors identify the variable without printing its value.

Reports count existing stored moderation cases by UTC calendar date, including
today's partial date, with zero-filled daily counts. Warnings include later-voided
records; timeout cases can include pending or failed attempts. Distinct members
are members referenced by cases, not the server's total membership. Reports
contain counts, dates, and the server ID, without message content, member IDs,
reasons, staff notes, or case outcomes. No new message tracking is introduced.
Historical coverage depends on the existing database; zero recorded cases does
not establish zero incidents. Guild removal deletes the underlying cases.

This is an additive pilot. Existing free moderation and community tools retain
their behavior. No checkout, subscriptions, paid entitlements, or billing webhook
are implemented, and no API accepts a client-supplied premium flag.

## Hosting handoff

1. Back up the existing database and `.env` on Chicago before updating.
2. Install the reviewed code and current requirements. Existing `.env` files
   need no edit for Harper's default pilot IDs; remove overriding variables if
   those defaults are intended. Set `PREMIUM_ENABLED=false` to disable access.
3. Restart Espresso. Startup creates an index on existing cases; no case rewrite
   or paid-data migration is needed. Slash commands sync through the normal
   global registration, or `DEV_GUILD_ID` if configured.
4. In an allowlisted server verify `/myid`, `/premium status`, and both report
   periods. In Overview verify period switching and that Refresh works.
5. Verify an unlisted staff account in an unlisted server is denied, and a member
   without staff permissions cannot use premium commands or read reports even
   in an allowlisted server.
6. Diagnose the existing Discord server-selection issue separately using the
   production logs and dashboard recovery PR #1. This premium branch starts from
   main and does not include that unmerged patch.

## Next steps toward a paid release

Validate report usefulness with pilot communities, then decide paid packaging.
Add verified server subscription entitlements, revocation/expiry handling,
idempotent billing events, and customer-facing subscription management before
charging. Keep allowlist grants separate from paid subscriptions. A scheduler
for opt-in staff digests and a report export can follow pilot feedback.
