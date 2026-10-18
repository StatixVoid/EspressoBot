# Server owners: invite, choose a log channel, configure

1. Use **Add Espresso** on the website. When OAuth is configured, the invite combines installation and Discord sign-in, then returns to the dashboard. Approve the requested server installation and `identify` / `guilds` access. Sign-in does not request email.
2. Choose a server you manage. New servers see the setup guide. The bot must be connected and installed; otherwise refresh after installation.
3. Choose a staff log channel, or explicitly choose **Keep channel logging off for now**. Create the private channel in Discord first. The menu disables channels where Espresso lacks View Channel, Send Messages, or Embed Links. The server checks those permissions again when saving. Selecting a channel does not change its privacy settings.
4. Choose **Balanced**, **Spam & invites only**, or **Keep my current filter settings**. Balanced enables the three identity-abuse categories at level 2 with removal and warnings. Both starter recipes use 6 messages / 8 seconds, block Discord invites, allow other links, and set escalation to a 10-minute timeout after 3 warnings within 24 hours. Existing custom lists and exemptions are preserved. Spam-only disables the identity categories.
5. Explicitly choose whether to enable automatic moderation. Saving with that box unchecked leaves moderation off. Save to open the full panel, then fine-tune categories and add reaction roles. Reaction roles have a separate enable switch.

Setup completion is stored per server. **Open setup guide** reopens it; returning to an already configured server defaults to preserving its current filters. Saving the advanced settings also finishes the guide. Settings changed through slash commands appear in the same panel. An older browser form must be refreshed after another save.

The full panel also includes **Activity log** and **Member review** tabs. Configure optional account screening in Settings and use Member review to publish the verification button and approve requests. See [MEMBER_SCREENING.md](MEMBER_SCREENING.md).

## Configure from Discord

Configuration requires Manage Server. The complete controls remain available as Discord slash commands:

```text
/espresso setup
/espresso logs channel:#your-private-staff-channel
/espresso filters block_invites:True block_links:False spam_enabled:True
/espresso spam limit:6 window_seconds:8
/espresso hate category:racism enabled:True level:2 action:warn
/espresso hate category:misogyny enabled:True level:2 action:warn
/espresso hate category:trans_hate enabled:True level:2 action:warn
/espresso escalation threshold:3 window_hours:24 timeout_minutes:10
/espresso enable enabled:True
/espresso settings
```

Discord autocompletes the exact arguments. Mention-based shortcuts are also supported: mention the actual bot, then type `setup` or `help` (for example, `@Espresso setup`). These return the website link and slash-command guide; they do not change settings or run free-form moderation commands. Replies are limited to once every 30 seconds per member per server. Slash-command setup replies are private. The mention shortcut's response is visible in its channel.

Detailed reaction-role mappings and per-category term exceptions are configured in the website. See [FILTERS_AND_ROLES.md](FILTERS_AND_ROLES.md).
