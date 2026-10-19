# Identity-based abuse filters and reaction roles

## Separate moderation categories

The panel provides independent **Racism**, **Misogyny**, and **Anti-trans hate** controls. Every category has an on/off switch, level 1–5, action, additional blocked terms, and exact-match exceptions. Categories start off so an existing server's behavior does not change on upgrade. Automatic moderation must also be enabled for them to run.

These are small, inspectable **English starter rules**, not a complete hate-speech classifier. They match configured words and phrases without determining the author's identity, beliefs, or intent. Neutral mentions such as “I am trans” are not themselves rules. Quotes, discussions of abuse, reclaimed language, and other contexts can still match. The source lists are in `espresso/hate_filters.py`, solely for detecting and moderating abuse. Review them for your community before enabling enforcement.

| Level | Cumulative coverage |
| --- | --- |
| 1 | Core explicit slurs from the category's starter list |
| 2 | Adds more slur vocabulary |
| 3 | Adds selected discriminatory and exclusionary phrase patterns |
| 4 | Adds spacing, punctuation/emoji gaps, leetspeak, repeated letters, selected Unicode lookalikes, invisible characters, and combining-mark evasions of slurs, covered phrases, and extra terms |
| 5 | Adds selected broader identity-directed insults and stereotypes, including combined evasions; highest false-positive risk |

Higher levels add rule coverage; the usual category action stays separate. The Unicode-disguise policy below overrides that action for confirmed disguised matches. This is not a guarantee to catch every slur, euphemism, spelling variation, contextual insult, or language. Phrase patterns are intentionally limited. Pronoun mismatches, speaker identity, and intent are not inferred.

## Click-to-reveal coverage examples

Under each coverage description, **Click for examples** opens uncensored, authored examples for that category and level. Higher levels include the earlier levels. Level 5 includes a combined example using lookalike letters, combining marks, leetspeak, and emoji separators. The readable phrase and expected policy are shown next to each disguised example.

Examples stay collapsed and are fetched only after opening the control. Changing the slider closes and clears the reveal, and loading a different server resets it. Keyboard users can open/close the native disclosure with Enter or Space. Opening examples never posts, saves settings, or applies moderation. The examples are checked against the actual detector in tests; no external AI service, runtime generation request, or user-message upload is involved.

## Unicode disguises and immediate timeouts

Only a match against an enabled abuse category can trigger this policy. Ordinary emoji, accented names, mathematical symbols, and other languages are allowed unless they themselves match a configured abuse rule. Unicode elsewhere in a message does not change the penalty for a plain-text match. Normal typographic apostrophes and whitespace are not sufficient to label a match as disguised.

When Unicode modifications are detected **inside a matched abuse span**, Espresso removes the message and attempts the configured timeout immediately, overriding that category's usual remove/warn action. The existing timeout duration is used; a warning threshold is not required. A newly created warning remains subject to its normal cooldown, but the Unicode timeout is independent of that cooldown. A source message receives at most one such timeout attempt, including duplicate gateway deliveries, edited messages, and process recovery. Editing a previously warned plain-text message into Unicode-disguised hate can trigger its first Unicode timeout. Self-deleting the offending message before Espresso's delete request finishes does not suppress this attempt.

Observation mode still records a hypothetical outcome without enforcing it. The master switch, role exemptions, owner/administrator protections, bot permissions, role hierarchy, and preservation of a longer current timeout still apply. Exact category exceptions still apply to the matched source span after the documented compatibility/case normalization. Staff should review the recorded outcome when Discord refuses the action.

Espresso records a timeout case before contacting Discord, then records the outcome. If the process stops in between, the case remains pending for staff to inspect; it is not automatically replayed. Case metadata contains the category, level, Unicode-evasion marker, member ID, and result, not the offensive message body.

## Matching scope and limits

The scanner folds a small, explicit set of visually similar characters and removes combining/format decorations for levels 4–5. It retains source spans so an exception for an ordinary word does not exempt every disguised variant. Compatibility-normalized and invisible-format variants already covered by levels 1–3 can also receive the Unicode timeout when they match.

Evasion patterns use word boundaries, bounded repetition/separators, and cached compiled patterns. They are applied to covered vocabulary and phrases, rather than blocking all non-ASCII text. Current matching permits up to 12 separator characters between matching units and caps identical runs to eight matching units while retaining the original source span. This improves common evasions while limiting expensive matching. It is not a complete Unicode confusable implementation, language classifier, image/OCR scanner, intent detector, or guarantee against novel bypasses. Semantic paraphrases, unlisted euphemisms, some character substitutions, and text split across messages can still require staff review and new rules.

Unicode normalization and visually confusable characters are distinct concerns; see [Python's Unicode normalization documentation](https://docs.python.org/3/library/unicodedata.html) and [Unicode's security mechanisms](https://www.unicode.org/reports/tr39/). Espresso's small mapping does not claim UTS #39 conformance.

Choose an action separately for each category:

- **Remove message only:** delete and log the matching message; no warning from that category.
- **Remove + warning:** delete, log, and issue a warning under the existing 30-second automatic-warning cooldown. Normal warning escalation applies.
- **Remove + warning + timeout:** issue a warning and attempt the configured timeout immediately, rather than waiting for the warning threshold. It uses the existing timeout duration and respects protected users, hierarchy, longer timeouts, and the automatic-warning cooldown.

If several identity categories match, the strongest selected action wins. Spam remains a separate warning rule. Global role exemptions, owner/administrator exemptions, and the automatic moderation master switch still apply.

Each category accepts up to 100 additional terms/phrases (literal at every level, with common evasion checks added at levels 4–5) and 100 exceptions, each 1–80 characters. Exceptions skip only an **exact matched span** in that category, after case/Unicode normalization. They do not whitelist the whole message, override other categories, or override the global blocked-word list. A singular exception is not an automatic exception for every plural or obfuscation. Do not enter regular expressions.

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
