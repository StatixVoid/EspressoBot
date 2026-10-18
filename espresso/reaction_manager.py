"""Durable, opt-in reaction roles on existing text-channel messages."""

import discord

from .reaction_roles import CUSTOM_EMOJI, emoji_key, mapping_key, role_error


class ReactionRoleManager:
    def __init__(self, bot):
        self.bot = bot

    async def validate(self, guild, actor, mappings):
        if not actor.guild_permissions.manage_roles:
            raise ValueError("You need Manage Roles to configure reaction roles.")
        roles = {role.id: role for role in await guild.fetch_roles()}
        channels = {channel.id: channel for channel in await guild.fetch_channels()}
        prepared = []
        for mapping in mappings:
            role = roles.get(mapping["role_id"])
            error = role_error(role, guild, guild.me, actor)
            if error:
                raise ValueError(error)
            channel = channels.get(mapping["channel_id"])
            if not isinstance(channel, discord.TextChannel):
                raise ValueError("Choose a text channel in this server for reaction roles.")
            perms = channel.permissions_for(guild.me)
            if not (perms.view_channel and perms.read_message_history and perms.add_reactions):
                raise ValueError("Espresso needs View Channel, Read Message History, and Add Reactions in that channel.")
            if not channel.permissions_for(actor).view_channel:
                raise ValueError("You must be able to view the selected reaction-role channel.")
            if CUSTOM_EMOJI.fullmatch(mapping["emoji"]):
                emoji_id = int(CUSTOM_EMOJI.fullmatch(mapping["emoji"])[1])
                if not any(emoji.id == emoji_id for emoji in guild.emojis):
                    raise ValueError("Choose a custom emoji belonging to this server, or use a Unicode emoji.")
            message = await channel.fetch_message(mapping["message_id"])
            prepared.append((message, mapping["emoji"]))
        return prepared

    async def seed(self, prepared) -> list[str]:
        failures = []
        for message, emoji in prepared:
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException as exc:
                failures.append(f"Message {message.id}: reaction could not be added (Discord HTTP {exc.status}).")
        return failures

    async def handle(self, payload, *, added: bool):
        if not payload.guild_id or payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        async with self.bot.member_lock(guild.id, payload.user_id):
            settings = await self.bot.store.settings(guild.id)
            if not settings.reaction_roles_enabled:
                return
            mapping = next((value for value in settings.reaction_roles
                            if value["message_id"] == payload.message_id and value["channel_id"] == payload.channel_id
                            and emoji_key(value["emoji"]) == emoji_key(str(payload.emoji))), None)
            if mapping is None or (not added and not mapping["remove_on_unreact"]):
                return
            if added and settings.screening["enabled"]:
                # A reaction must never bypass the staff-reviewed access role.
                if mapping["role_id"] == settings.screening["verified_role_id"]:
                    return
            key = mapping_key(mapping)
            if not added and not await self.bot.store.role_grant(guild.id, key, payload.user_id):
                return  # Never revoke a role the member already had from another source.
            try:
                # Recheck the role's current permissions/hierarchy on every event.
                roles = {role.id: role for role in await guild.fetch_roles()}
                role = roles.get(mapping["role_id"])
                error = role_error(role, guild, guild.me)
                if error:
                    await self.bot.log_action(guild, "Reaction role skipped", f"Member {payload.user_id} · role {mapping['role_id']}\n{error}")
                    return
                member = await guild.fetch_member(payload.user_id)
                if member.bot:
                    return
                if added and settings.screening["enabled"] and (getattr(member,"pending",False) or not any(r.id == settings.screening["verified_role_id"] for r in member.roles)):
                    return  # Other community roles are offered after verification too.
                has_role = any(value.id == role.id for value in member.roles)
                if added:
                    if has_role:
                        return
                    await member.add_roles(role, reason="Espresso: opted in through a reaction", atomic=True)
                    await self.bot.store.track_role_grant(guild.id, key, member.id, role.id)
                else:
                    if has_role:
                        await member.remove_roles(role, reason="Espresso: reaction removed", atomic=True)
                    await self.bot.store.forget_role_grant(guild.id, key, member.id)
                await self.bot.log_action(guild, "Reaction role granted" if added else "Reaction role removed",
                                          f"Member {member.id} · role {role.id} · message {payload.message_id}.")
            except discord.HTTPException as exc:
                await self.bot.log_action(guild, "Reaction role failed",
                                          f"Member {payload.user_id} · role {mapping['role_id']} · Discord HTTP {exc.status}.")
