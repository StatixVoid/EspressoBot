# Identity-based abuse filters and reaction roles

## Separate moderation categories

The panel provides independent **Racism**, **Misogyny**, and **Anti-trans hate** controls. Every category has an on/off switch, level 1–5, action, additional blocked terms, and exact-match exceptions. Categories start off so an existing server's behavior does not change on upgrade. Automatic moderation must also be enabled for them to run.

These are small, inspectable **English starter rules**, not a complete hate-speech classifier. They match configured words and phrases without determining the author's identity, beliefs, or intent. Neutral mentions such as “I am trans” are not themselves rules. Quotes, discussions of abuse, reclaimed language, and other contexts can still match. The source lists are in `espresso/hate_filters.py`, solely for detecting and moderating abuse. Review them for your community before enabling enforcement.

| Level | Cumulative coverage |
| --- | --- |
| 1 | Core explicit slurs from the category's starter list |
| 2 | Adds more slur vocabulary |
| 3 | Adds selected discriminatory and exclusionary phrase patterns |
| 4 | Adds common spaced, punctuated, and leetspeak evasions of the starter slurs |
| 5 | Adds selected broader identity-directed insults and stereotypes; highest false-positive risk |

Higher levels add rule coverage; **they do not increase punishment automatically**. This is not a guarantee to catch every slur, euphemism, spelling variation, contextual insult, or language. Phrase patterns are intentionally limited. Pronoun mismatches, speaker identity, and intent are not inferred.

Choose an action separately for each category:

- **Remove message only:** delete and log the matching message; no warning from that category.
- **Remove + warning:** delete, log, and issue a warning under the existing 30-second automatic-warning cooldown. Normal warning escalation applies.
- **Remove + warning + timeout:** issue a warning and attempt the configured timeout immediately, rather than waiting for the warning threshold. It uses the existing timeout duration and respects protected users, hierarchy, longer timeouts, and the automatic-warning cooldown.

If several identity categories match, the strongest selected action wins. Spam remains a separate warning rule. Global role exemptions, owner/administrator exemptions, and the automatic moderation master switch still apply.

Each category accepts up to 100 additional literal terms/phrases and 100 exceptions, each 1–80 characters. Exceptions skip only an **exact matched span** in that category, after case/Unicode normalization. They do not whitelist the whole message, override other categories, or override the global blocked-word list. A singular exception is not an automatic exception for every plural or obfuscation. Do not enter regular expressions.

Use the panel for detailed lists, or set the main controls through:

```text
/espresso hate category:misogyny enabled:True level:3 action:warn
```

The settings download includes every category configuration. Warnings/logs identify the category and configured level; they do not copy the offensive message into the database.

## Reaction roles

In the panel's **Reaction roles** section:

1. Turn on Reaction roles. This switch is independent of automatic moderation.
2. Choose **Add a reaction role**.
3. Select an existing text channel and enter the message ID. Enable Discord Developer Mode, then right-click the message to copy its ID.
4. Choose a Unicode emoji (such as ✅ or ☕), or a custom emoji from this server in `<:name:id>` format.
5. Select a regular community role and choose whether removing the reaction should remove that role.
6. Save. Espresso verifies access to the message and tries to add its own reaction so members have something to click. If that last step fails, the save response explains it; fix permissions or emoji and retry setup.

Configuration requires **Manage Server and Manage Roles** for the signed-in user. The user's highest role must be above each selected role unless the user owns the server. Espresso requires **Manage Roles** and a highest role above every role it assigns. In the message channel it also needs **View Channel**, **Read Message History**, and **Add Reactions**.

The normal invite link now includes Manage Roles and Add Reactions. Existing installations may need their bot role permissions updated. No privileged Server Members intent is required: reaction events use uncached raw handlers and REST member lookup.

Everyone/integration-managed roles and roles with administration, moderation, role/channel/message management, or mass-mention powers cannot be offered. Runtime checks repeat before assignments so later role-permission changes cannot turn an existing mapping into a staff-role grant.

Up to 25 mappings are allowed per server. Each role and each message/emoji trigger may appear only once. Reaction adds grant roles; individual reaction removals optionally revoke only assignments Espresso recorded. A role the member already held when reacting is not recorded or revoked by that mapping. Assignments are persisted across process restarts. Bot reactions are ignored.

Disabling/deleting a mapping, deleting its message, or clearing all reactions does **not** mass-revoke existing roles. Mapping deletion discards its ownership records. Events that occur while Espresso is offline are not backfilled; members may need to remove and re-add their reaction after it reconnects. Review opt-in roles during server cleanup. Role changes and failures are sent to the configured action log.

Role grants are not a way to configure Discord Linked Roles; the Linked Roles Verification URL in the Developer Portal remains blank.
