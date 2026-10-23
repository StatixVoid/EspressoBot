# Espresso community tools and staff desk

All features are included in the same Python process and SQLite database. Existing server settings are preserved on upgrade. Automatic database migrations add the inbox, cases, scheduled posts, and temporary raid recovery records. Back up the database while Espresso is stopped before upgrading, then restart the full app to register the new Discord commands.

See [the refinement guide](REFINEMENTS.md) for expanded workflows, calendar scheduling, simulations, rollback, and role menus.

## Private reports and appeals

Members use `/report member reason [message_url]`, or the message context menu's **Report to Espresso** action. A linked message must be in the same server and accessible to the reporter. The bot saves the submitted explanation, reporter/target IDs, and message link, without copying the reported message's body. Submissions are private responses. Closed DMs do not block intake. Each member may have five open submissions and submit at most once per minute per server.

Warnings include a case number in the member's DM. `/mycases` shows a member only their own recent cases. `/appeal case_number reason` accepts only that member's case in the current server, with one open appeal per case.

Open **Staff desk** in the dashboard to assign an item to yourself, resolve or reject it with an internal note, or reopen it. A separate member-facing response is available through `/myreports` and can optionally be sent by DM. Internal notes stay private. Review history records the staff member, action, note, and time. Version checks prevent stale pages from overwriting another review. Staff cannot review their own submission. Resolving an appeal records a decision; staff explicitly remove a mistaken warning or timeout separately.

Dashboard access supports Manage Server or Moderate Members. Moderators receive Overview and Staff desk without configuration access. The inbox, cases, and warning removal require Moderate Members, rechecked on every request. Moderators without Manage Server can use `/staffdesk inbox`, `/staffdesk review`, `/staffdesk cases`, and `/staffdesk removewarning`. Notes and report contents are not copied to channel or process logs.

## Channel rules, trusted links, and observation mode

In **Settings → A more personal recipe**, add lowercase trusted domains such as `youtube.com`. Links to that domain and its subdomains pass the general link filter; a lookalike such as `youtube.com.evil.example` does not. Trusted domains do not bypass blocked phrases or the separate Discord invite filter. This is a hostname allowlist, not a malware scanner; redirects and uploaded files are not scanned.

Channel rules override link, invite, and spam switches, or skip automatic moderation for a channel. Other settings inherit the server's configuration. Threads inherit their parent's rule when no explicit rule exists. Server-wide moderation must still be enabled. Exempt roles continue to bypass automatic moderation. Raid detection is configured separately and still monitors message events in channels that skip normal filters.

**Observation mode** logs matched filters without deleting messages, warning members, or applying automatic timeouts. Raid responses become alerts. Member screening, welcomes, scheduled posts, and explicit manual staff commands retain their own behavior. A server should choose a staff log channel to receive raid alerts in Discord; action metadata also appears in Activity log.

In **Community & tools → Try a filter**, paste text and optionally choose a channel. Choose saved settings or the current unsaved form and provide hypothetical warning counts, recent-message counts, and roles, or a member ID for current roles and timeout hierarchy. The result explains exemptions, cooldowns, observation, and escalation without saving settings, sending/deleting a message, or recording a case. Live timing and Discord failures remain outside the simulation. `/community testfilter` supports the same context inputs with saved settings.

## Moderation cases and warning expiry

New warnings, automatic deletions without a new warning (including warning cooldowns), timeout attempts, timeout removal, warning clearing/removal, and raid responses have case numbers. Existing warnings are backfilled into case history on upgrade. Cases contain the actor, target where applicable, reason, outcome, and timestamp. A failed timeout is recorded as an attempt with its actual result, not as a successful penalty.

In Staff desk, search by member ID and page through case history. Removing an individual warning requires a reason and the same member hierarchy protections as manual moderation. For a departed member, removal additionally requires Manage Server. Removed warnings stop counting but retain an audit record. Existing timeouts remain until staff explicitly remove them.

**Warning expiry (days)** is optional: 0 uses the existing rolling escalation window with no additional expiry. With a positive value, a warning counts only while it is within both that age and the configured escalation window. Expiry never applies penalties retroactively or deletes case history. Warning clearing removes warning rows and retains case/removal audit metadata.

## Raid detection and temporary protection

Raid protection starts off. Enable Server Members Intent in the Discord Developer Portal and `ENABLE_MEMBER_SCREENING_EVENTS=true` on the host before enabling it. Configure a join threshold, rolling detection window, and number of distinct members repeating the same normalized message. Short messages under eight characters are excluded from coordinated-repeat detection. Bots and normal moderation exemptions are ignored for message-based detection.

Choose **Alert staff**, **Temporary slowmode**, or **Temporary lockdown**. A join spike targets accessible text channels; repeated messages target the affected text channel, or its parent text channel for a thread. Alerts are throttled to one response per configured protection duration per server. Detection counters reset after restart.

Slowmode requires Manage Channels and keeps any longer existing delay. Lockdown requires Manage Roles and temporarily denies @everyone sending messages, posting in threads, and creating threads. Explicit allows on other roles or members, and administrators, can still bypass those restrictions. Espresso does not rewrite other role overwrites or promise a server-wide access freeze.

The old values and expiry are saved before each Discord write. The background worker restores expired protection every 15 seconds while connected, including after restarts. Only permission bits or slowmode values still matching Espresso's imposed settings are restored, preserving subsequent differing staff edits. A later staff edit to the exact same value cannot be distinguished from Espresso's value. A failed restoration stays in the recovery queue, reports an issue, and retries once per minute. Run **End temporary protection** or `/community endprotection` to retry early. Removing Espresso prevents it from restoring Discord settings; end protection before removing the bot.

## Health checks

**Community & tools → Server health** and `/community health` check connection status, moderation permissions, requested gateway intents, configured posting channels, reaction/verification role hierarchy, raid response permissions, and outstanding restoration errors. The verification check looks for a text channel hidden from @everyone and visible to the Verified role. It is a useful diagnostic, not a full audit of every member's roles or access path.

## Welcomes, posts, reminders, and notification roles

Enable **Welcome cards**, choose a text channel, and customize the message using `{member}` and `{server}`. Cards include the new member's avatar. Mentions render without sending notifications. Welcomes require member events and run only on joins received while online.

In **Community & tools**, schedule an announcement or event reminder, choose its channel, and select a local date/time and named timezone. Choose once, a fixed interval, or daily/weekly local-clock repeats in a named timezone. The calendar and post editor support pause/resume and reviewed delivery recovery. After downtime, at most one overdue occurrence is sent and missed repeat occurrences are skipped. The worker checks due posts every 15 seconds while connected. Up to 100 pending posts per server are supported. Pending posts can be cancelled; a post already being delivered cannot be cancelled.

`/community schedule channel when message [repeat_days] [notify]` accepts an ISO timestamp with an explicit timezone, such as `2026-12-01T18:00-07:00`. `/community posts` lists status and `/community cancelpost` cancels pending delivery. Both scheduling paths require Manage Server and access to post in the selected channel. Those permissions are rechecked before delivery, so a removed or demoted author cannot leave privileged scheduled posts running.

Messages suppress all mentions by default. Configure an existing, ordinary, mentionable notification role in Settings, and use reaction roles to let members opt in. Only posts explicitly marked **Notify role** may ping that role; user and @everyone pings stay suppressed. No roles are created or granted automatically by the scheduler.

Delivery is claimed persistently before sending. A restart during delivery, timeout, or ambiguous network failure is shown as **uncertain** and is not automatically replayed. Check the Discord channel before scheduling a replacement. This favors avoiding duplicate announcements over retrying an unconfirmed send. Repeats stop on failed or uncertain delivery and need staff review. The recovery editor requires a future time; uncertain deliveries also require explicit confirmation that staff checked Discord first.

## Coffee cards, polls, and daily prompts

Each feature starts off and is optional per server:

- `/coffee member [drink] [character]` sends a friendly illustrated café card, with a 30-second per-member cooldown and no notification pings. Choose from 12 real drinks, including espresso, cold brew, hot chocolate, chai, and matcha, or leave the drink as `surprise`. Autocomplete lists the supported beverage names. Hot chocolate is coffee-free; cocoa still contains some caffeine.
- `/drinks [member]` opens a private beverage menu with drink and character selectors. Choose a recipient or leave it empty for a treat for yourself, then press **Send card** to post in the current channel. The menu lasts three minutes; sending rechecks whether cards remain enabled and whether you and Espresso can post. Invalid choices and missing permissions do not use your cooldown.
- Cards can feature Sabrina, Velvet Guardian, Cloud Dreamer, Honey Spark, or **Just the drink**. Staff choose the server's default character alongside the card's style, color, title, and message in Community settings. Character artwork ships with the bot and requires **Attach Files**; all cards require **Embed Links**. Simple cards work without attachment permission. Existing server settings inherit Sabrina as their default character.
- `/poll question options [hours]` creates a native Discord poll. Separate 2–10 unique answer options with `|`; duration is 1–168 hours. A member may start one poll every two minutes. Both the member and bot need channel posting and Send Polls permissions.
- **Daily conversation starters** post a rotating café prompt in the chosen channel. Select the hour in UTC; 16 UTC is 9 AM in Arizona. The first connected worker tick after that hour queues one prompt for the current UTC day. It skips previous days and persists the date to avoid duplicate prompts after restart. Moving or disabling prompts before delivery cancels the queued prompt.

Coffee cards support configurable styles, titles, text, and a custom accent color. Daily prompts can use a server-written library or the built-in fallback. Persistent button/dropdown role menus are also available in Community & tools; see [the refinement guide](REFINEMENTS.md#button-and-dropdown-role-menus). Coffee cards and polls require both the member and Espresso to be able to post in the channel. These commands cannot turn a read-only channel into a public posting route.

## Data and operation

The staff inbox stores submitted text, member IDs, linked message IDs/URLs, optional case references, assignments, decisions, and review notes. Moderation cases and warning-removal records preserve reasons and outcomes. Scheduled posts store their message text, channel/actor IDs, due/repeat times, notification choice, and delivery status. Recovery records store only the changed permission values, channel IDs, and restoration deadline. Repeated-message detection retains bounded text hashes and member/time buckets in memory, not original text in SQLite.

These records remain until server data is removed or the operator handles a deletion request. Warning expiry affects escalation eligibility, not data retention. The existing 30-day/10,000-entry Activity log policy is separate. When Espresso receives a server-removal event it clears all these server records, including jobs and recovery data. Offline removals still need operator cleanup. Backups and Discord messages require separate retention handling.

The automated tests use mocked Discord calls. Finish a private-server check before enabling automatic raid restrictions in a real community. Editing this checkout does not update a separate running server; deploy the source and restart as described in [PUBLIC_SERVER.md](PUBLIC_SERVER.md).
