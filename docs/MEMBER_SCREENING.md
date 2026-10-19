# Member screening and manual verification

Screening is optional per server and independent of message moderation. It checks Discord account age, collects an explicit rules agreement when required, and withholds a configured access role until the policy passes. Existing members' roles are not removed. Server owners, administrators, and bots are not screened.

## One-time host setup

Enable **Server Members Intent** under Discord Developer Portal → Bot → Privileged Gateway Intents, keeping Message Content Intent enabled. Then set this in the **NUC's private `.env`** and restart the full app:

```dotenv
ENABLE_MEMBER_SCREENING_EVENTS=true
```

The flag defaults to false so existing moderation-only installations keep working. Discord's portal switch and this host setting are both required. Add the setting on the computer running the public bot, then restart Espresso; enabling it on a separate development computer does not update the server. Git pulls preserve the private `.env` and do not add this setting automatically. Presence Intent is unnecessary. This does not change OAuth scopes or require Administrator. See [discord.py's member intent documentation](https://discordpy.readthedocs.io/en/stable/intents.html#member-intent).

## Configure each server on the website

1. Create a regular **Verified** role in Discord, below Espresso and the staff who approve it. It must have no administration, moderation, management, or mass-mention powers.
2. Prepare channel access: let `@everyone` view the welcome/verification channel; hide protected categories from `@everyone`; grant the Verified role permission to view those categories. Audit other roles and channel overrides that might already grant access. Espresso does not rewrite channel permissions or remove roles.
3. In the website's **Settings → A thoughtful welcome**, choose the Verified role and entry channel. Enable member screening.
4. Select **Every new member** for manual approval of everyone, or **Only accounts below the minimum age**, using a 1–365 day account-age threshold. Account age is measured at join time. A flagged account stays held for review as it ages.
5. Enter rules and choose whether agreement is required. With age-based screening, older accounts can receive the role automatically after any required agreement. If agreement is off, eligible older accounts can be approved on join. New accounts still need staff approval. Account age is a signal, not proof of safety.
6. Save, open **Member review**, and choose **Post verification button**. This explicitly posts a new message in the saved entry channel. Check for an existing copy before posting another.

Members click **Read rules & verify**, read the current rules in a private response, and explicitly choose **I agree & request access**. They may also use `/verify`. Old welcome buttons always open the current rules; old agreements are rejected after the rules change. The entry button keeps working after restarts.

Discord's own rules screening is respected: no access role is granted while the member's Discord `pending` flag is true. They must complete that screening first and retry `/verify`. See [Discord membership screening](https://docs.discord.com/developers/resources/guild#membership-screening-object).

## Staff review

The website's **Member review** tab shows current requests, account age at join, agreement time/status, new-account flags, member IDs, and Discord profile links. Staff with **Manage Server and Manage Roles** can approve, decline, or reopen requests. The actor's and bot's role hierarchy are checked. Staff cannot approve their own request or grant a privileged access role.

Approval requires the current agreement when enabled and rechecks the member, native Discord screening, role permissions, and request version. Declining withholds access without kicking, banning, or removing roles. A declined request needs staff reopening before another approval. The review screen does not revoke already approved roles; manage those explicitly in Discord.

Discord alternatives:

```text
/espresso screening
/screening publish
/screening queue
/screening review member:@Member action:approve
/screening review member:@Member action:deny
/screening review member:@Member action:reopen
/verify
```

`/espresso screening` configures the main controls; custom rules text is edited on the website. The configured access role cannot be a reaction-role mapping. Other Espresso reaction roles are withheld until verification, preventing those mappings from opening access prematurely. Other bots and manually granted roles must be checked separately.

## Persistence and limits

Requests, current agreement hashes/times, role/decision metadata, and staff audit records persist in SQLite. Confirmed departures leave the queue; rejoining creates a fresh request and invalidates old agreement/review versions. Schema additions preserve existing settings and warnings. Stop and back up the database before deploying updates.

There is no bulk revocation or automatic scan of members who joined while Espresso was offline. Existing members without the access role can use `/verify`. Join/leave events are processed while online. Cases and decision audit records remain until server data is removed or the operator handles deletion. Discord actions and SQLite cannot form one transaction; if a role grant is interrupted, check the member's actual roles and refresh before retrying. Role additions are idempotent.

## Website activity logs

The **Activity log** tab shows new moderation, settings, reaction-role, and verification actions, even with Discord channel logging disabled. Filter by category, refresh, or load older entries in pages of 50. It requires current server management access; it is not public.

Website history records new events while Espresso is running. Earlier Discord log messages and process logs are not imported. Entries contain action metadata rather than original message text, manual warning reasons, tokens, or cookies. The retained window is 30 days, bounded to 10,000 entries per server; expiry is filtered on reads and pruned on startup and new writes. Warning history and screening case/audit retention are separate. Backup copies require their own retention policy.

The server picker collapses on phones. Keyboard-accessible Settings, Activity log, and Member review tabs remain available while scrolling. Temporary Discord read failures receive a bounded retry; a visible **Retry this server** control handles failures that still need attention. Permission responses are not cached across requests.
