"""Reviewed, opt-in role menus. Discord writes are journaled and never replayed blindly."""

import asyncio
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import re
import unicodedata

import discord

from .reaction_roles import CUSTOM_EMOJI, emoji_key, role_error, validate_mappings
from .rules import Settings
from .store import SettingsConflict

LOG = logging.getLogger("espresso.templates")
CATALOG = json.loads((Path(__file__).with_name("public") / "role-templates.json").read_text(encoding="utf-8"))


def clean_text(value, limit, label):
    if (type(value) is not str or not value.strip() or len(value.strip()) > limit
            or any(unicodedata.category(c) == "Cc" and c != "\n" for c in value)
            or "@" in value or "<" in value or ">" in value):
        raise ValueError(f"{label}: use 1–{limit} characters without mentions or control characters.")
    return value.strip()


def validate_request(data):
    fields = {"request_id", "version", "template_id", "channel_id", "title", "description", "remove_on_unreact", "options"}
    if type(data) is not dict or set(data) != fields:
        raise ValueError("Invalid template request.")
    if type(data["request_id"]) is not str or not re.fullmatch(r"[a-f0-9]{32}", data["request_id"]):
        raise ValueError("Invalid template request ID. Reload the panel.")
    if type(data["version"]) is not int or data["version"] < 0:
        raise ValueError("Invalid settings version.")
    if data["template_id"] not in {item["id"] for item in CATALOG}:
        raise ValueError("Choose a supported template.")
    def identifier(value):
        if type(value) is not str or not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**63:
            raise ValueError("Choose a channel or role in this server.")
        return int(value)
    result = {**data, "channel_id": identifier(data["channel_id"]),
              "title": clean_text(data["title"], 120, "Embed title"),
              "description": clean_text(data["description"], 600, "Embed description")}
    if type(data["remove_on_unreact"]) is not bool or type(data["options"]) is not list or not 1 <= len(data["options"]) <= 20:
        raise ValueError("Choose 1–20 options per message. Discord allows 20 different reactions on a message.")
    result["options"] = []
    names, emojis, roles = set(), set(), set()
    for option in data["options"]:
        if type(option) is not dict or set(option) != {"name", "emoji", "role_id"}:
            raise ValueError("Invalid template option.")
        name = clean_text(option["name"], 60, "Role label")
        if "\n" in name:
            raise ValueError("Keep role labels on one line.")
        role_id = identifier(option["role_id"]) if option["role_id"] is not None else None
        emoji = option["emoji"]
        validate_mappings([{"channel_id":1,"message_id":1,"role_id":1,"emoji":emoji,"remove_on_unreact":True}])
        if name.casefold() in names or emoji_key(emoji) in emojis or (role_id is not None and role_id in roles):
            raise ValueError("Each option needs a different label, reaction, and role.")
        names.add(name.casefold()); emojis.add(emoji_key(emoji)); roles.add(role_id)
        result["options"].append({"name":name,"emoji":emoji,"role_id":role_id})
    return result


class TemplatePublisher:
    def __init__(self, store, bot):
        self.store, self.bot = store, bot
        # Bounded locks avoid unbounded server-ID growth; no database lock is held over HTTP.
        self.locks = [asyncio.Lock() for _ in range(64)]

    async def publish(self, guild, actor, raw):
        data = validate_request(raw)
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        async with self.locks[guild.id % len(self.locks)]:
            if not actor.guild_permissions.manage_roles:
                raise ValueError("You need Manage Server and Manage Roles to post role templates.")
            previous = await self.store.template_operation(guild.id, data["request_id"])
            if previous:
                if previous["actor_id"] != actor.id or previous["fingerprint"] != fingerprint:
                    raise ValueError("This request ID belongs to different choices. Start a new template.")
                if previous["state"] == "posting":
                    previous = {**previous, "state":"interrupted", "error":"The previous post was interrupted. Check the selected channel and server roles before starting a new template; this request will not post twice."}
                return previous
            settings, version = await self.store.snapshot(guild.id)
            if version != data["version"]:
                raise SettingsConflict("Settings changed elsewhere. Refresh this server before posting a template.")
            if len(settings.reaction_roles) + len(data["options"]) > 25:
                raise ValueError("Use at most 25 reaction-role mappings across this server. Remove options or old mappings first.")
            roles = {role.id:role for role in await guild.fetch_roles()}
            channels = {channel.id:channel for channel in await guild.fetch_channels()}
            channel = channels.get(data["channel_id"])
            if not isinstance(channel, discord.TextChannel) or guild.me is None:
                raise ValueError("Choose a text channel in this server.")
            perms = channel.permissions_for(guild.me)
            if not all(getattr(perms, flag, False) for flag in ("view_channel", "send_messages", "embed_links", "read_message_history", "add_reactions")):
                raise ValueError("Espresso needs View Channel, Send Messages, Embed Links, Read Message History, and Add Reactions in this channel.")
            if not channel.permissions_for(actor).view_channel:
                raise ValueError("You must be able to view the selected channel.")
            if not guild.me.guild_permissions.manage_roles:
                raise ValueError("Espresso needs Manage Roles to post a template.")
            used = {mapping["role_id"] for mapping in settings.reaction_roles}
            selected, create = [], []
            for option in data["options"]:
                custom = CUSTOM_EMOJI.fullmatch(option["emoji"])
                if custom and not any(emoji.id == int(custom[1]) for emoji in guild.emojis):
                    raise ValueError("Use a Unicode emoji or a custom emoji from this server.")
                if option["role_id"] is None:
                    if any(role.name.casefold() == option["name"].casefold() for role in roles.values()):
                        raise ValueError(f"A role named {option['name']} already exists. Select that role explicitly or change the label.")
                    selected.append(None); create.append(option)
                else:
                    role = roles.get(option["role_id"])
                    error = role_error(role, guild, guild.me, actor)
                    if error:
                        raise ValueError(error)
                    if role.id in used or role.id == settings.screening["verified_role_id"]:
                        raise ValueError("A role is already mapped or reserved for member verification. Choose another role.")
                    selected.append(role)
            if len(roles) + len(create) > 250:
                raise ValueError("This template would exceed Discord's server role limit. Reuse existing roles or remove options.")
            operation = {"actor_id":actor.id,"fingerprint":fingerprint,"state":"posting",
                         "channel_id":str(channel.id),"message_id":None,"created_role_ids":[],"warnings":[]}
            await self.store.begin_template_operation(guild.id, data["request_id"], version, operation)
            created, message, committed = [], None, False
            try:
                for index, option in enumerate(data["options"]):
                    if selected[index] is None:
                        role = await guild.create_role(name=option["name"], permissions=discord.Permissions.none(),
                                                       hoist=False, mentionable=False, reason=f"Espresso role template · moderator {actor.id}")
                        created.append(role); selected[index] = role
                        operation["created_role_ids"].append(str(role.id))
                        await self.store.write_template_operation(guild.id, data["request_id"], operation)
                        error = role_error(role, guild, guild.me, actor)
                        if error:
                            raise ValueError(error)
                # Recheck roles and actor before publishing, including roles created above.
                actor = await guild.fetch_member(actor.id)
                fresh = {role.id:role for role in await guild.fetch_roles()}
                for role in selected:
                    error = role_error(fresh.get(role.id), guild, guild.me, actor)
                    if error:
                        raise ValueError(error)
                embed = discord.Embed(title=data["title"], description=data["description"], colour=0x85509E)
                lines = [f"{option['emoji']}  {discord.utils.escape_markdown(option['name'])} · <@&{role.id}>" for option,role in zip(data["options"],selected)]
                chunk = []
                for line in lines:
                    if chunk and len("\n".join(chunk + [line])) > 1000:
                        embed.add_field(name="React to choose your roles" if not embed.fields else "More choices", value="\n".join(chunk), inline=False)
                        chunk = []
                    chunk.append(line)
                embed.add_field(name="React to choose your roles" if not embed.fields else "More choices", value="\n".join(chunk), inline=False)
                note = "Remove your reaction to remove a role Espresso gave you." if data["remove_on_unreact"] else "Removing a reaction keeps the role. Ask staff to remove it."
                embed.set_footer(text="Espresso Bot · All choices are optional. " + note)
                message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                operation["message_id"] = str(message.id)
                await self.store.write_template_operation(guild.id, data["request_id"], operation)
                mappings = [{"channel_id":channel.id,"message_id":message.id,"emoji":option["emoji"],"role_id":role.id,
                             "remove_on_unreact":data["remove_on_unreact"]} for option,role in zip(data["options"],selected)]
                changes = {"reaction_roles_enabled":True,"reaction_roles":settings.reaction_roles + mappings}
                Settings(**(asdict(settings) | changes))
                operation["state"] = "complete"
                operation["warnings"] = ["Check the posted message; reaction setup may not have finished. Add missing reactions manually if needed."]
                await self.store.commit_template_operation(guild.id, data["request_id"], version, actor.id, changes, operation)
                committed = True
                operation["warnings"] = await self.bot.reaction_manager.seed([(message,option["emoji"]) for option in data["options"]])
                if operation["warnings"]:
                    operation["warnings"].append("Mappings are saved. Add any missing reactions to this message manually, or switch reaction roles off and back on and save to reseed them.")
                await self.store.write_template_operation(guild.id, data["request_id"], operation)
            except Exception as exc:
                LOG.warning("Template post failed: guild=%s request=%s type=%s", guild.id, data["request_id"], type(exc).__name__)
                if committed:
                    operation["state"] = "complete"
                    operation["warnings"] = ["The menu and mappings are saved. Check the message and add any missing reactions manually."]
                else:
                    operation["state"] = "failed"
                    operation["error"] = "The template could not finish. Known new resources were rolled back where possible. Check the channel and roles before starting another post."
                    if isinstance(exc, (ValueError, SettingsConflict)):
                        operation["error"] = str(exc) + " The unfinished template was rolled back where possible."
                    for resource in ([message] if message else []) + list(reversed(created)):
                        try:
                            if resource is message:
                                await resource.delete()
                            else:
                                await resource.delete(reason="Espresso: rollback unfinished role template")
                        except Exception:
                            operation["warnings"].append(f"Could not remove {type(resource).__name__} {resource.id}; staff must check it in Discord.")
                    # A failed network response can hide a successful Discord create; never replay.
                    if not isinstance(exc, (ValueError, SettingsConflict)):
                        operation["warnings"].append("Discord may have accepted a request before the connection failed. Inspect the channel and role list before trying a new post.")
                await self.store.write_template_operation(guild.id, data["request_id"], operation)
            try:
                await self.bot.log_action(guild, "Reaction role template posted" if operation["state"] == "complete" else "Reaction role template failed",
                                          f"Moderator {actor.id} · template {data['template_id']} · channel {channel.id} · message {operation['message_id'] or 'none'}. " + " ".join(operation["warnings"]))
            except Exception:
                LOG.warning("Template audit delivery failed: guild=%s", guild.id)
            return operation
