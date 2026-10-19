# Dashboard refinements

This update adds report follow-through, a moderator dashboard, calendar scheduling and post editing, a moderation simulator, raid controls, settings history, an overview, persistent role menus, coffee-card styling, and custom prompts. Restart Espresso after deploying so `/myreports` and the expanded commands register. The launcher installs the pinned `tzdata` package, which supplies named timezones on Windows.

## Reports and moderator access

Members use `/myreports [before]` to read only their own report/appeal status and member-facing response. Older items can be paged by the last displayed ID. Internal notes, assignments, reported content, and other members' submissions are not included in this command.

When resolving or rejecting an inbox item, staff write a required **internal staff note** and an optional, separate **member-facing outcome**. They can optionally send the outcome by DM. The DM includes the item, server, final status, and member-facing text; internal notes never enter it. Closed DMs do not undo the review. Delivery status appears in the staff desk, and `/myreports` remains available. An interrupted or uncertain DM is not automatically replayed. Reopening clears the current member-facing response while retaining the private review audit.

Members with **Moderate Members** can sign into the dashboard and see **Overview** and **Staff desk**, without Manage Server. Each report/case request rechecks Discord membership and Moderate Members. Configuration, history, schedules, health diagnostics, role menus, and verification setup remain manager-only. Staff without management access receive no settings, roles, channel configuration, scheduled message text, or settings snapshots in their server bootstrap response. Manage Server alone does not grant access to private reports without Moderate Members.

Open items older than 48 hours receive a waiting badge. This is an attention indicator, not a promised response deadline.

## Calendar scheduling, edits, and delivery recovery

Choose a named IANA timezone such as `America/Phoenix` or `America/New_York`, then a local date/time. The scheduler offers:

- Send once.
- Daily at the selected local clock time.
- Weekly on selected weekdays at the selected local clock time.
- A fixed interval of 1–365 days; 0 means once for compatibility with earlier schedules.

Daily and weekly repeats follow timezone clock changes. A nonexistent spring-forward wall time is rejected for the first occurrence; later repeats skip that day's nonexistent occurrence. An ambiguous fall-back wall time uses its first occurrence once. The pinned timezone database should be updated as jurisdictional timezone rules change. Missed repeat occurrences are skipped after a single overdue delivery. Fixed intervals remain exact 24-hour multiples.

The calendar projects pending and paused occurrences for the visible month, in the browser's displayed timezone. Each post retains its own schedule timezone. A response is bounded to 1,000 projected occurrences; the interface shows when that limit is reached. Paused occurrences are projections and will not be sent.

Pending posts can be paused, edited, or cancelled. Paused posts can be edited, resumed, or cancelled. Editing does not resume a paused post. Resume requires a future date, so an overdue paused announcement is not sent unexpectedly. The post's version protects against stale editing; a claimed/in-progress delivery cannot be edited. The latest editor becomes the scheduling author, whose current management and channel permissions are checked again at delivery.

Failed or uncertain posts have a recovery editor. Choose a new future time and review the text. An uncertain post additionally requires **I checked Discord and want a new delivery attempt** before retrying. The scheduler never blindly replays an ambiguous send. A repeat that failed or became uncertain needs this explicit recovery before it continues.

`/community schedule` still accepts an ISO timestamp with an explicit offset. It also accepts `timezone_name`, `recurrence` (`once`, `interval`, `daily`, `weekly`), and comma-separated `weekdays` (Monday 0 through Sunday 6). Daily/weekly local-clock repeats require a named timezone. Post editing, pause/resume, and delivery recovery are in **Community & tools**.

## Moderation simulator

Choose **Saved settings** or **Current unsaved form**, enter sample text, and select a channel. Optionally choose hypothetical roles or provide a member ID. A member ID uses fresh Discord roles, administrator/owner/bot exemptions, and timeout hierarchy; it overrides hypothetical roles. Warning count and recent-message count are explicitly hypothetical inputs, not inferred member history.

The simulator follows enabled/disabled channel rules, exempt roles, text filters, the selected spam count, edited-message behavior, observation mode, automatic warning cooldown, configured escalation, and timeout hierarchy. It explains the likely outcome and the active warning count after the event. A longer existing timeout would be preserved. No message is sent/deleted and no warning, timeout, case, or settings write occurs.

`/community testfilter` supports `member`, `active_warnings`, `recent_messages`, `warning_cooldown`, and `edited` using saved settings. The dashboard additionally supports hypothetical roles and unsaved settings. These are simulations: actual event timing, warning deduplication, permission changes, and Discord errors can change live results.

## Raid exclusions and active protection

Choose text channels or categories excluded from automatic raid restrictions. They still contribute to detection. A thread targets its parent text channel, whose exclusions apply. Exclusions do not discard already saved restoration records.

Overview and Community & tools show active protection, affected channel IDs, and a countdown. Staff can end each channel's protection or extend its timer by ten minutes, up to one hour from now. The original restoration snapshot is preserved. Expired countdowns say **Awaiting restoration** until the worker confirms completion. Failed restorations stay queued, with a visible explanation and existing bounded retries. Managers can still end all temporary protection together.

## Settings history and rollback

Every saved recipe has a full settings snapshot, version, changed fields, time, and actor when available. Dashboard and configuration slash-command writes record their actor. The first imported snapshot may have no actor or timestamp. History starts at this update; earlier states cannot be reconstructed from old changed-key audit records.

Choose **Preview restore** to see the exact current/earlier values. Restoring creates a new version through the normal settings save route. It rechecks current permissions, channel access, reaction-role/verification hierarchy, and version conflicts. Newer history is retained. Save or reset an unsaved form before restoring. Published role menus, report decisions, Discord messages, scheduled jobs, and active raid recovery records are separate operational state and are not reversed by restoring the recipe.

## Overview

Moderators see waiting report/appeal counts, items waiting over 48 hours, the oldest waiting time, and aggregate automatic-warning rule counts for the last seven days. Managers additionally see verification request counts, failed/uncertain scheduled posts, setup issues, and active protection. Links lead to the relevant desk or tools. Aggregate counts do not copy message bodies into analytics. Refresh requests recheck access; countdowns update locally between reads.

## Button and dropdown role menus

Open **Community & tools → Community role menus**. Choose a channel, title, description, button/dropdown style, and up to 25 existing roles with labels and descriptions. Set a minimum and maximum selection count. Maximum 1 creates a choose-one group. Buttons support descriptions in the accompanying embed; dropdowns display them as option descriptions.

Publishing requires Manage Server and Manage Roles, channel posting access, and a staff/bot hierarchy above every selected role. Default, managed, privileged/staff, mass-mention, and verified access roles are rejected. Each menu is saved before posting; a lost/uncertain delivery is listed for staff to check rather than posted again automatically. The same request ID is idempotent. Retire an uncertain request before creating a replacement.

Menu controls persist across restarts. Every click reloads the menu, member, role definitions and sensitive permission checks. Discord screening and Espresso verification are respected. The menu changes only the requesting member's roles. It tracks roles it granted and refuses to remove roles granted by staff or another menu. A choose-one change replaces only menu-owned choices. Limits account for roles already present. If Discord partially applies a role change, ownership is reconciled against the actual member roles and the member is asked to check/retry; staff may need to correct a partial selection.

Disable a menu to retire its controls. Server-side checks reject stale controls even if Discord cannot update the message. Existing member roles remain. Up to 25 active/posting/uncertain menus per server are supported. To change published choices, disable the old menu and publish a reviewed replacement; ordinary role changes outside menus still require staff review.

## Coffee designs and prompt library

In Settings, choose Classic rose, Pastel lavender, Midnight café, or a custom six-digit embed color. Customize the card title and message with `{sender}`, `{member}`, and `{drink}`. A local card preview shows the text and accent. `/coffee` retains its existing cooldown, channel permission checks, and suppressed mention notifications.

The custom conversation prompt library accepts up to 100 lines of 1–300 characters each. Daily prompts rotate through this library when nonempty; an empty library uses Espresso's built-in prompts. The existing daily UTC posting hour and persisted date continue to prevent repeated daily posts after restart.

## Data and verification

New records include full settings snapshots, report member-facing responses and notice status, named-zone recurrence/edit versions, role-menu configuration/status, and menu-granted role ownership. They are scoped to the server and removed with its existing server cleanup. Clearing warnings does not delete settings/report/menu audit records. Backups and messages already sent to Discord require separate deletion handling.

Tests cover timezone boundaries, permissions, private/internal response separation, stale edits and restores, non-enforcing simulation, role-menu ownership and persistent controls, and raid exclusions/recovery snapshots. Browser tests use isolated fixtures to exercise manager and moderator views on desktop and mobile. Live Discord actions still need a private-server check after deployment.
