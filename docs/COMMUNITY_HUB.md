# Community hub

This update adds eight independent community features. Restart Espresso to register the new commands and start its hub worker. Sign into the dashboard as a server manager and open **Community hub**. All eight features start disabled. The hub has its own save button and version; saving the moderation recipe does not save hub changes. Settings history currently covers the moderation recipe, not hub settings.

The dashboard includes configuration, a paged activity list, ticket claiming/closing, suggestion updates, event creation/cancellation, FAQ editing, badge awards, and delivery issues. Every request rechecks current Discord membership and Manage Server. Moderators without Manage Server keep their limited Staff desk and cannot read or configure the hub. Private ticket transcripts never appear in dashboard list responses.

## Setup and permissions

| Feature | Setup | Espresso permissions |
| --- | --- | --- |
| Tickets | Support category, support team role, support labels; optionally allow transcripts | View Channel, Manage Channels, Manage Roles, Send Messages, Embed Links, Read Message History; Attach Files for transcript delivery |
| Events | Enable events; optional dedicated event role | Create Events or Manage Events; Manage Roles and suitable hierarchy for a temporary role |
| Suggestions | Choose a board text channel | View Channel, Send Messages, Embed Links, Read Message History |
| Voice rooms | Join-to-create voice lobby, room category, default user limit | Move Members, Connect, View Channel, Manage Channels, Manage Roles |
| FAQ | Enable saved replies and write entries | View Channel and Send Messages where commands are used |
| Birthdays | Greeting channel, IANA timezone, local hour; optional dedicated birthday role | Standard text permissions; Manage Roles and suitable hierarchy for a temporary role |
| Highlights | Public board channel, up to 25 public source text channels, 2–50 stars | Standard text permissions in source and board channels |
| Profiles | Enable café profiles | Standard command/channel access; no role permission is needed for badges |

The installation link requests these optional permissions without Administrator. Existing installations may need an updated authorization or role permissions. Put Espresso above temporary roles. Configuring temporary roles also requires the manager to have Manage Roles and a higher role. Event creation and cancellation require the manager's Manage Events permission in addition to Manage Server.

Voice state and scheduled event gateway intents are enabled automatically. The privileged Members intent is not required for these features; join protection, welcome messages, and existing member screening keep their host requirements. Community participation rechecks member status and respects Discord screening and Espresso's verified access role. Privacy removal commands continue working when their feature is disabled.

## Support tickets

Members use `/ticket open category:General subject:...`. The support label has autocomplete. Each member can have one active ticket and open five tickets in a rolling 24 hours. A server can have 100 active tickets. Rooms explicitly deny @everyone and allow the author, configured support role, and Espresso. Server administrators retain Discord's normal access. Category permission inheritance is not used to grant unrelated roles access.

The author may request `save_transcript:True` when opening a ticket if the server allows it. The default is false. The room's welcome explains its transcript setting. Enabling server transcript storage later does not opt existing tickets in. Disabling storage prevents new capture even for a ticket that requested it.

`/ticket mine [before]` lists the author's ticket numbers privately. Support-role members and managers use `/ticket claim ticket_id:...`; a ticket already claimed by another support member cannot be taken over. The author or support team uses `/ticket close ticket_id:... reason:...`. Close reasons and staff assignments are retained. A claimed ticket may be closed by another support member if needed.

Closing first records the intent. When requested and allowed, it reads the latest 1,000 messages into a plain text transcript (450 KB message-text budget), saves it, then deletes the room. Attachment links are included and may expire; attachments and embedded media are not downloaded. Failure to read the requested transcript leaves the room intact. Failed deletion leaves a closing record that can be retried. Espresso checks its room topic marker before deleting a ticket channel.

`/ticket transcript ticket_id:...` privately downloads the text for the author or current support team. Transcripts expire after 30 days. Closed ticket subjects, close reasons, and transcripts are purged together after that period; minimal ticket metadata remains. Discord messages already deleted cannot be reconstructed.

An interrupted room creation is not blindly repeated. The worker can recover an existing room by its exact Espresso topic marker. For unresolved uncertain requests, staff must inspect Discord before choosing **Retire request** in the hub. Retiring the record does not delete an unknown Discord room.

## Events and reminders

Managers create events in the dashboard or with `/events create name:... location:... starts:... ends:... timezone_name:... description:...`. Slash commands accept ISO date/times with an explicit offset or a local date/time plus an IANA timezone. Dashboard date fields use the timezone displayed above them. Start between 10 seconds and one year from now and end within seven days of the start.

These are native, server-only Discord scheduled events with an external location; the location can describe a server gathering or link. Members can use Discord's event interest list separately from Espresso reminders. Manage native event details in Discord; Espresso refreshes start/end times and cancellation/completion status. See [Discord's scheduled event documentation](https://docs.discord.com/developers/resources/guild-scheduled-event).

`/events list` shows Espresso event numbers and links. `/events reminders event_id:... enabled:True` explicitly subscribes to a DM during the 15 minutes before the event and to any configured temporary event role. Subscribe before that window ends; there is no late reminder after the start. Each event supports 250 reminder subscriptions. Espresso sends at most once per member and scheduled start time. Closed DMs, uncertain deliveries, and restarts do not trigger automatic resend. Delivery issues appear in the hub. Subscribing does not change Discord's native interest list.

The event's dedicated role is assigned to opted-in members while the native event is active, and removed after it ends or is cancelled. `/events reminders ... enabled:False` unsubscribes and queues removal, including when events are disabled. `/events end event_id:...` or the dashboard cancels a scheduled event or completes an active one. A server supports 100 active event records. Uncertain event creation requires staff inspection; no automatic second event is created.

## Suggestion board

`/suggest idea:...` posts an idea with persistent Upvote and Downvote buttons. A member has one vote per idea: choosing the other direction replaces it, and choosing the same direction removes it. Authors may vote. Bots cannot participate. Voting rechecks current membership, verification, and board access. Buttons work after restart.

Members can submit three ideas in a rolling 24 hours. A server supports 250 unresolved suggestions. Staff use the dashboard or `/suggestions status suggestion_id:... status:... note:...`. Statuses are open, under review, accepted, planned, completed, and declined. The staff note is public. Completed and declined suggestions close voting; reopening enables voting again. Votes and statuses are durable even if updating the Discord embed fails, so refresh the hub before retrying an update.

## Temporary voice rooms

Joining the lobby creates a personal room and moves the member into it. The new room copies the lobby's permission overwrites and explicitly allows its owner and Espresso. Choose a lobby whose visibility matches the intended audience. No Manage Channels permission is granted to the owner.

Owners use `/room rename name:...`, `/room limit count:...`, and `/room privacy locked:True`. Locking denies @everyone Connect; staff and roles with explicit Connect access may still join. Unlocking restores the lobby's original @everyone Connect setting. The room owner stays the owner until it is cleaned up; ownership is not automatically transferred to another occupant.

Empty rooms are deleted on voice changes or the next worker pass. Occupied rooms remain when the owner leaves. Cleanup uses channel voice states and persisted room IDs, including after restart, and never deletes unrelated channels. Disabling room creation still permits empty-room cleanup. There are at most 30 active rooms per server and one per owner. A member can create five rooms in a rolling ten minutes, so repeated joining and leaving does not bypass creation limits. Uncertain creation can be recovered by its original unique room name and category; otherwise staff must inspect it before retiring its record.

## FAQ and saved replies

Managers write up to 100 replies in the hub. Names use 1–32 lowercase letters, numbers, underscores, or hyphens; summaries are up to 80 characters and answers up to 1,800 characters. Select an existing reply to edit/delete it, or choose **New reply**. Versions protect against overwriting another staff member's edits.

Members use `/faq name:...` or `/tag name:...`, with searchable name/summary autocomplete. Replies are posted publicly with mention notifications suppressed and link embeds disabled. These are staff-written responses, not generated answers or automatic message scanning.

## Birthday celebrations

`/birthday set month:... day:...` opts into a public greeting and any configured birthday role. Only month and day are stored. `/birthday remove` deletes the date and queues removal of any birthday role Espresso granted. Dates are not listed in the dashboard or on café profiles.

Greetings run at or after the chosen local hour, once per member per local date. February 29 is observed on February 28 in other years. The member must still belong to the server, pass screening, and be able to view the greeting channel. Missed days are skipped; restarting after the greeting hour can deliver that day's unsent greeting. Up to 100 birthdays are processed per worker pass. Messages suppress mention notifications. Birthday roles expire at the next local midnight.

## Community highlights

Espresso recounts ⭐ reactions on messages in approved source channels. Two to 50 unique human reactions are needed, depending on the threshold; self-stars and bots do not count. Recounts inspect at most 100 reacting users. Source and board must both be visible to @everyone. Private threads, restricted channels, bot/webhook messages, and copying NSFW content into a non-NSFW board are excluded.

The highlight contains a bounded text excerpt and the original message link. It does not copy attachments. A falling star count removes the post. Removed highlights stay removed rather than automatically being reposted. Edits refresh a posted excerpt; source deletion removes its highlight. The worker also rechecks source visibility and opt-outs.

`/highlights optout enabled:True` persists the member's server-specific opt-out and removes existing tracked highlights. `enabled:False` allows future highlights without reposting previously removed ones. Managers use the dashboard or `/highlights remove highlight_id:...` to remove and block a post. Each server supports 200 active highlight records. Uncertain posts are not automatically reposted. Privacy removal attempts to locate an uncertain post among the latest 200 board messages using its exact source link; unresolved results require staff inspection. Discord outages or changed permissions can delay removal, which is retried by the worker.

## Café profiles, thanks, and badges

`/profile edit bio:... theme:rose|lavender|mocha` creates an optional public café card. `/profile show [member]` displays its bio, avatar, total received thanks, and up to 12 badges. `/profile delete` removes the profile, badges, and visible thanks history. The bot does not award message-count experience.

Both sender and recipient must opt into profiles before `/thanks member:...` can award a thank-you. A sender can give three thanks per rolling 24 hours, once per recipient; self-awards and bot awards are rejected. Limit checks are atomic. Deleting/recreating a profile does not reset the limit: a separate award-limit ledger expires after 24 hours.

Managers award or remove named event/helpfulness badges using the hub or `/events badge member:... name:... remove:...`. Awards require an opted-in profile; attendance is assessed by staff rather than inferred from voice connection time.

## Reliability and stored data

The worker runs approximately once a minute while the bot is ready. It reconciles room creation, cleans empty voice rooms, updates native event timing, delivers reminders/greetings, removes expired owned roles, and removes highlights that no longer qualify for privacy. Temporary roles already held before the bot's action are preserved. Roles that become privileged, exceed the bot's hierarchy, or are reused by verification/role menus need staff review before automated removal. Use dedicated roles and avoid changing their purpose while grants are active.

Stored records include hub settings/version/actor, room and message IDs, ticket subjects and requested transcripts, ideas/statuses/votes, event metadata/reminder subscriptions and delivery outcomes, voice room ownership, birthday month/day, profiles/thanks/badges, highlight opt-outs, saved replies, and temporary role ownership. All are server scoped and included in server-removal cleanup. Settings rollback does not undo these operations. Sent Discord messages, audit logs, files downloaded by staff, and backups have their own deletion handling.

Tests exercise permissions, server isolation, private transcript access, uncertainty/restart behavior, named-zone birthday timing, closed DMs, vote changes, ownership and cleanup, award-limit abuse, and desktop/mobile workflows with isolated fixtures. Live Discord permission and delivery checks should be performed in a private server after deployment.
