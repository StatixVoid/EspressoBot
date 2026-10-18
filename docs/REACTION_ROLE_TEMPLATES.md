# Reaction-role templates

Open your server's dashboard → **Reaction roles → Start with a role template**.

1. Save or reset any unsaved server settings first.
2. Choose a text channel and a template: **Gender**, **Pronouns**, **Country / region**, **Interests**, **Notification preferences**, or **Color labels**.
3. Edit the embed title/introduction, reactions, and labels. Uncheck unwanted options, remove options, or add your own. The country list is a starting point, not a complete list of countries or nationalities.
4. For each option, explicitly choose an existing role or leave **Create a basic role** selected. Espresso will not silently reuse a role with the same name; select it explicitly or rename your option.
5. Choose whether removing a reaction removes a role granted by Espresso. Select **Review template**, check the proposed message and role changes, then **Add template to channel**.
6. Follow **Open the posted message in Discord**. The embed, reactions, and mappings are ready for members. The generated mappings also appear under **Use an existing message instead**, where you can edit or remove them.

Posting enables reaction roles for this server, including any saved older mappings. It does not turn automatic message moderation on. You can post different menus in different channels, within the shared limit of 25 mappings per server. A single message supports at most 20 options because Discord limits distinct reactions on a message to 20.

All choices are optional and member-selected. Espresso does not infer anyone's gender, pronouns, or nationality. Members can choose multiple options; these menus do not enforce a single exclusive choice. Color templates create named roles; set actual colors and order in Discord afterward. New roles have no extra permissions, are not hoisted, and are not mentionable. Staff with the appropriate Discord permission can mention notification roles themselves.

## Permissions and access

The person posting needs **Manage Server** (or Administrator), **Manage Roles**, access to the channel, and a role above each existing role being assigned. Espresso needs **Manage Roles** and a role above the roles it gives. In the selected channel it needs **View Channel, Send Messages, Embed Links, Read Message History, and Add Reactions**. No new OAuth scope or gateway intent is needed for templates.

Existing roles with staff, administration, or mass-mention permissions are rejected. A role can only belong to one mapping. The member-screening access role is reserved even when screening is disabled. When screening is enabled, other community reaction roles are available only after verification. Channel permissions remain controlled in Discord.

Removing a reaction only revokes a role Espresso tracked as granting through that mapping. It leaves pre-existing roles alone. Deleting a mapping does not delete its embed or remove members' existing roles. Delete the message or unused roles manually in Discord if you want to retire a menu completely. Reactions made while the bot is offline are not backfilled; members should remove and add their reaction again after it reconnects.

## Failed or interrupted posts

Each reviewed post has a unique request ID persisted in SQLite before any Discord writes. Double clicks and a retry of the same request return its recorded result, including after a process restart; they do not start the menu again. Successful outcome, mappings, settings version, and configuration audit are saved together. Discord reactions are added after mappings are committed.

The tab keeps an unconfirmed request locally. If the connection fails, use **Check / retry previous post**. If the menu was already saved, it returns the existing Discord message and current settings. Keep this browser tab open until the result is confirmed; closing it removes its local draft.

Known pre-save failures roll back only the new message and roles created by that attempt, as far as Discord allows. A settings-version conflict rolls back the unfinished menu rather than overwriting another moderator's settings. Existing roles and messages are not deleted. If Discord accepted a create operation but its response was lost, or the process stopped mid-post, the outcome can be uncertain. Espresso refuses to replay that request blindly. Inspect the selected channel and roles using the recorded IDs, remove any unfinished resources, refresh the server, then select **I checked Discord · start a new request** if you want to try again. Database and Discord changes cannot form one transaction.

If mappings were saved but some reactions failed, the response reports warnings. Add the missing reactions manually, or switch reaction roles off and back on and save to reseed configured reactions. Do not post another copy just to retry reactions.

Template metadata (actor ID, choice hash, generated IDs, and outcome) remains until server data is removed. It contains no OAuth or bot credentials. Existing deployments use the same database; the new journal table is created automatically at startup. Do not run multiple Espresso bot processes against one server/database.

Implementation uses discord.py's documented [role creation and message APIs](https://discordpy.readthedocs.io/en/stable/api.html#discord.Guild.create_role).
