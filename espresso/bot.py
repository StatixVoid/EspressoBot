"""Gateway events, slash commands, and Discord moderation actions."""

import asyncio
from dataclasses import asdict
from datetime import timedelta
import io
import json
import logging
import time
from typing import Literal

import discord
from discord import app_commands

from .rules import Settings, SpamTracker, content_decision, normalize
from .hate_filters import ContentDecision
from .reaction_manager import ReactionRoleManager
from .store import Store

LOG = logging.getLogger("espresso")
COLOR = 0xD8A7B1  # Soft pink, with a little coffee-shop charm.


class UserError(app_commands.AppCommandError):
    pass


def safe(text: str, limit: int = 900) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))[:limit]


def target_error(actor: discord.Member | None, target: discord.Member,
                 me: discord.Member | None) -> str | None:
    guild = target.guild
    if target.bot or target.id == guild.owner_id or target.guild_permissions.administrator:
        return "Bots, the server owner, and administrators are protected."
    if actor is not None:
        if actor.id == target.id:
            return "You cannot moderate yourself."
        if actor.id != guild.owner_id and actor.top_role <= target.top_role:
            return "Your highest role must be above this member's highest role."
    if me is None or me.top_role <= target.top_role:
        return "Espresso's highest role must be above this member's highest role."
    return None


class StaffGroup(app_commands.Group):
    def __init__(self, permission: str, **kwargs):
        super().__init__(default_permissions=discord.Permissions(**{permission: True}),
                         guild_only=True, **kwargs)
        self.permission = permission

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise UserError("Use this command in a server.")
        # Default command permissions are only UI defaults; enforce again at runtime.
        if not getattr(interaction.user.guild_permissions, self.permission):
            raise UserError(f"You need the {self.permission.replace('_', ' ')} permission.")
        return True


class Espresso(discord.Client):
    def __init__(self, database_path: str, dev_guild_id: int | None = None):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.guild_reactions = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(),
                         max_messages=1000, member_cache_flags=discord.MemberCacheFlags.none(),
                         activity=discord.Game(name="Espresso | keeping chats sweet ☕"))
        self.store = Store(database_path)
        self.dev_guild_id = dev_guild_id
        self.spam = SpamTracker()
        self.reaction_manager = ReactionRoleManager(self)
        # Fixed-size locks serialize one member's actions without unbounded lock storage.
        self.member_locks = [asyncio.Lock() for _ in range(128)]
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.command_error
        self.register_commands()

    def member_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self.member_locks[hash((guild_id, user_id)) % len(self.member_locks)]

    async def setup_hook(self) -> None:
        await self.store.open()
        if self.dev_guild_id:
            guild = discord.Object(id=self.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        LOG.info("Slash commands registered (%s).", "development server" if self.dev_guild_id else "global")

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            await self.store.close()

    async def on_ready(self) -> None:
        LOG.info("Espresso ready as user %s in %s server(s).", self.user.id, len(self.guilds))

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        # Do not dump event payloads, message content, or credentials into logs.
        LOG.error("Event handler failed: %s; check database availability and permissions.", event_method)

    async def command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        cause = getattr(error, "original", error)
        if isinstance(cause, (UserError, ValueError)):
            message = str(cause)
        elif isinstance(cause, discord.Forbidden):
            message = "Discord refused this action. Check Espresso's permissions and role position."
        elif isinstance(cause, discord.HTTPException):
            message = "Discord could not complete the action. Check its status before retrying."
        elif isinstance(cause, app_commands.CheckFailure):
            message = "You do not have permission to use this command."
        else:
            LOG.error("Command failed (%s).", type(cause).__name__)
            message = "The command failed. Check the bot's database and server logs before retrying."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def log_action(self, guild: discord.Guild, title: str, details: str) -> bool:
        # Local metadata log remains useful if a Discord log channel is unavailable.
        LOG.info("guild=%s action=%s %s", guild.id, title, details)
        settings = await self.store.settings(guild.id)
        if settings.log_channel is None:
            return False
        channel = guild.get_channel(settings.log_channel)
        if not isinstance(channel, discord.TextChannel):
            LOG.warning("Log channel missing: guild=%s", guild.id)
            return False
        embed = discord.Embed(title=f"☕ {title}", description=details[:4000], color=COLOR,
                              timestamp=discord.utils.utcnow())
        try:
            await channel.send(embed=embed)
            return True
        except discord.HTTPException as exc:
            LOG.warning("Log delivery failed: guild=%s status=%s", guild.id, exc.status)
            return False

    async def set_timeout(self, member: discord.Member, minutes: int,
                          reason: str, *, preserve_longer: bool = True) -> str:
        # Member caching is disabled. Re-read the current timeout so stale command
        # objects cannot shorten a longer timeout issued concurrently by staff.
        try:
            member = await member.guild.fetch_member(member.id)
        except discord.HTTPException as exc:
            return f"Timeout failed: could not refresh member (Discord HTTP {exc.status})."
        me = member.guild.me
        failure = target_error(None, member, me)
        if failure:
            return f"Timeout skipped: {failure}"
        if not me.guild_permissions.moderate_members:
            return "Timeout failed: Espresso needs Moderate Members."
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        if preserve_longer and member.timed_out_until and member.timed_out_until >= until:
            return "Existing longer timeout kept."
        try:
            await member.timeout(until, reason=reason[:512])
            return f"Timed out for {minutes} minute(s)."
        except discord.HTTPException as exc:
            return f"Timeout failed (Discord HTTP {exc.status}); check permissions and role position."

    async def issue_warning(self, guild: discord.Guild, member: discord.Member,
                            moderator_id: int, reason: str, source: str,
                            settings: Settings, automatic: bool = False, force_timeout: bool = False) -> str | None:
        warning_id, count = await self.store.warn(
            guild.id, member.id, moderator_id, reason, source,
            settings.warn_window_hours, automatic=automatic,
        )
        if warning_id is None:
            return None
        escalation = "No timeout."
        if force_timeout or (settings.warn_threshold and count >= settings.warn_threshold):
            escalation = await self.set_timeout(member, settings.timeout_minutes,
                                                f"Espresso {'category action' if force_timeout else 'warning threshold'}; case {warning_id}")
        # Do not copy manual reasons into process logs or the shared channel.
        await self.log_action(guild, "Warning recorded",
                              f"Case #{warning_id} · member {member.id} · moderator {moderator_id}\n"
                              f"{count} warning(s) in {settings.warn_window_hours}h. {escalation}")
        # Best effort DM: failure does not prevent enforcing moderation.
        try:
            await member.send(f"☕ Espresso: you received a warning in {safe(guild.name, 100)}.\n"
                              f"Reason: {safe(reason, 300)}\n{escalation}")
        except discord.HTTPException:
            pass
        return f"Warning #{warning_id} saved. {count} in the active window. {escalation}"

    async def on_message(self, message: discord.Message) -> None:
        await self.moderate_message(message, edited=False)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        # Fetch uncached edits too. Ignore embed-only updates.
        if payload.guild_id is None or "content" not in payload.data:
            return
        guild = self.get_guild(payload.guild_id)
        if not guild:
            return
        try:
            channel = guild.get_channel_or_thread(payload.channel_id)
            if channel is None:
                channel = await self.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException as exc:
            LOG.warning("Edit could not be read: guild=%s status=%s", guild.id, exc.status)
            return
        await self.moderate_message(message, edited=True)

    async def moderate_message(self, message: discord.Message, *, edited: bool) -> None:
        guild = message.guild
        if guild is None or message.author.bot or message.webhook_id is not None or message.is_system():
            return
        async with self.member_lock(guild.id, message.author.id):
            settings = await self.store.settings(guild.id)
            if not settings.enabled:
                return
            member = message.author
            if not isinstance(member, discord.Member):
                try:
                    member = await guild.fetch_member(member.id)
                except discord.HTTPException:
                    return
            if (member.id == guild.owner_id or member.guild_permissions.administrator
                    or any(role.id in settings.exempt_roles for role in member.roles)):
                return
            # Content deletion can still apply when the bot cannot timeout this role.
            spam = (not edited and settings.spam_enabled and self.spam.hit(
                guild.id, member.id, time.monotonic(), settings.spam_limit, settings.spam_window))
            decision = content_decision(message.content, settings)
            if spam and (decision is None or decision.action == "delete"):
                decision = ContentDecision("Message spam")
            if decision is None:
                return
            violation = decision.reason
            try:
                await message.delete()
                deletion = "Message deleted."
            except discord.NotFound:
                # Another moderator or bot already handled this message.
                return
            except discord.HTTPException as exc:
                deletion = f"Deletion failed (Discord HTTP {exc.status}); check Manage Messages."
            await self.log_action(guild, "Automatic filter",
                                  f"Member {member.id} · channel {message.channel.id} · message {message.id}\n"
                                  f"Rule: {violation}. {deletion}")
            if decision.action != "delete":
                await self.issue_warning(guild, member, self.user.id, violation,
                                         f"message:{message.id}", settings, automatic=True,
                                         force_timeout=decision.action == "timeout")

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self.reaction_manager.handle(payload, added=True)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.reaction_manager.handle(payload, added=False)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.store.delete_guild(guild.id)
        self.spam.reset_guild(guild.id)

    def register_commands(self) -> None:
        config = StaffGroup("manage_guild", name="espresso", description="Brew your server's moderation settings")
        mod = StaffGroup("moderate_members", name="mod", description="Espresso staff moderation")

        @config.command(name="settings", description="Show this server's settings and filter lists")
        async def settings(interaction: discord.Interaction):
            value = await self.store.settings(interaction.guild_id)
            embed = discord.Embed(title="☕ Espresso · server recipe", color=COLOR)
            embed.description = (
                f"Automatic moderation: **{'on' if value.enabled else 'off'}**\n"
                f"Spam: {value.spam_enabled} · {value.spam_limit} messages / {value.spam_window}s\n"
                f"Block links: {value.block_links} · Block invites: {value.block_invites}\n"
                f"Escalation: {value.warn_threshold or 'disabled'} warnings in {value.warn_window_hours}h"
                f" → {value.timeout_minutes} minute timeout\n"
                f"Log channel: {f'<#{value.log_channel}>' if value.log_channel else 'not set'}"
            )
            embed.add_field(name=f"Blocked phrases ({len(value.bad_words)}; first 10)",
                            value="\n".join(safe(w, 80) for w in value.bad_words[:10]) or "None", inline=False)
            embed.add_field(name=f"Exempt roles ({len(value.exempt_roles)}; first 20)",
                            value=", ".join(str(r) for r in value.exempt_roles[:20]) or "None", inline=False)
            embed.add_field(name="Identity-based abuse filters", value="\n".join(
                f"{category}: {'level ' + str(config['level']) + ' · ' + config['action'] if config['enabled'] else 'off'}"
                for category, config in value.hate_filters.items()), inline=False)
            embed.add_field(name="Reaction roles", value=f"{'On' if value.reaction_roles_enabled else 'Off'} · {len(value.reaction_roles)} mapping(s)", inline=False)
            embed.set_footer(text="Keep it sweet. Unofficial fan-inspired bot.")
            recipe = json.dumps(asdict(value), indent=2, ensure_ascii=False).encode("utf-8")
            await interaction.response.send_message(
                embed=embed, file=discord.File(io.BytesIO(recipe), filename="espresso-settings.json"), ephemeral=True)

        @config.command(name="enable", description="Turn automatic moderation on or off")
        async def enable(interaction: discord.Interaction, enabled: bool):
            await self.store.update(interaction.guild_id, enabled=enabled)
            self.spam.reset_guild(interaction.guild_id)
            await interaction.response.send_message(f"☕ Automatic moderation {'enabled' if enabled else 'disabled'}.", ephemeral=True)
            await self.log_action(interaction.guild, "Settings changed", f"Moderator {interaction.user.id} set enabled={enabled}.")

        @config.command(name="filters", description="Configure links, Discord invites, and spam detection")
        async def filters(interaction: discord.Interaction, block_links: bool | None = None,
                          block_invites: bool | None = None, spam_enabled: bool | None = None):
            changes = {k: v for k, v in dict(block_links=block_links, block_invites=block_invites,
                                             spam_enabled=spam_enabled).items() if v is not None}
            await self.store.update(interaction.guild_id, **changes)
            self.spam.reset_guild(interaction.guild_id)
            await interaction.response.send_message("Filter settings saved. Use /espresso settings to review.", ephemeral=True)
            await self.log_action(interaction.guild, "Filters changed", f"Moderator {interaction.user.id} updated filter toggles.")

        @config.command(name="spam", description="Set the message count and rolling time window")
        async def spam(interaction: discord.Interaction, limit: app_commands.Range[int, 2, 50],
                       window_seconds: app_commands.Range[int, 2, 60]):
            await self.store.update(interaction.guild_id, spam_limit=limit, spam_window=window_seconds)
            self.spam.reset_guild(interaction.guild_id)
            await interaction.response.send_message(f"Spam triggers at {limit} messages in {window_seconds}s across server channels.", ephemeral=True)
            await self.log_action(interaction.guild, "Spam settings changed", f"Moderator {interaction.user.id} set limit={limit}, window={window_seconds}s.")

        @config.command(name="hate", description="Configure an identity-based abuse category; levels add rule coverage")
        async def hate(interaction: discord.Interaction, category: Literal["racism", "misogyny", "trans_hate"],
                       enabled: bool, level: app_commands.Range[int, 1, 5], action: Literal["delete", "warn", "timeout"] = "warn"):
            await self.store.edit_hate_filter(interaction.guild_id, category, enabled=enabled, level=level, action=action)
            await interaction.response.send_message(f"{category}: {'on' if enabled else 'off'}, level {level}, action {action}. Advanced term lists are in the web panel.", ephemeral=True)
            await self.log_action(interaction.guild, "Abuse filter changed", f"Moderator {interaction.user.id} · {category} · enabled={enabled}, level={level}, action={action}.")

        @config.command(name="escalation", description="Set warning threshold, window, and timeout (threshold 0 disables)")
        async def escalation(interaction: discord.Interaction, threshold: app_commands.Range[int, 0, 20],
                             window_hours: app_commands.Range[int, 1, 720],
                             timeout_minutes: app_commands.Range[int, 1, 40320]):
            await self.store.update(interaction.guild_id, warn_threshold=threshold,
                                    warn_window_hours=window_hours, timeout_minutes=timeout_minutes)
            await interaction.response.send_message("Warning escalation saved. Applies when the next warning is issued.", ephemeral=True)
            await self.log_action(interaction.guild, "Escalation changed", f"Moderator {interaction.user.id}: threshold={threshold}, window={window_hours}h, timeout={timeout_minutes}m.")

        @config.command(name="word", description="Add or remove a literal blocked word or phrase")
        async def word(interaction: discord.Interaction, action: Literal["add", "remove"],
                       phrase: app_commands.Range[str, 1, 80]):
            phrase = normalize(phrase).strip()
            if not phrase:
                raise UserError("Enter a visible word or phrase.")
            await self.store.edit_list(interaction.guild_id, "bad_words", phrase, action == "add")
            await interaction.response.send_message("Blocked phrase list saved.", ephemeral=True)
            await self.log_action(interaction.guild, "Blocked phrases changed", f"Moderator {interaction.user.id} used {action}.")

        @config.command(name="exempt", description="Add or remove an automatic-moderation role exemption")
        async def exempt(interaction: discord.Interaction, action: Literal["add", "remove"], role: discord.Role):
            if role.is_default():
                raise UserError("The everyone role cannot be exempted. Use /espresso enable to pause moderation.")
            await self.store.edit_list(interaction.guild_id, "exempt_roles", role.id, action == "add")
            await interaction.response.send_message("Role exemptions saved. Staff commands still apply.", ephemeral=True)
            await self.log_action(interaction.guild, "Exemptions changed", f"Moderator {interaction.user.id}: {action} role {role.id}.")

        @config.command(name="logs", description="Choose a private staff log channel, or disable channel logging")
        async def logs(interaction: discord.Interaction, channel: discord.TextChannel | None = None,
                       disabled: bool = False):
            if disabled:
                await self.log_action(interaction.guild, "Channel logging disabled", f"Moderator {interaction.user.id} disabled channel logs.")
                await self.store.update(interaction.guild_id, log_channel=None)
                await interaction.response.send_message("Channel logging disabled. Process metadata logs remain active.", ephemeral=True)
                return
            if channel is None:
                raise UserError("Choose a text channel, or set disabled to True.")
            if channel.guild.id != interaction.guild_id:
                raise UserError("Choose a channel from this server.")
            permissions = channel.permissions_for(interaction.guild.me)
            if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
                raise UserError("Espresso needs View Channel, Send Messages, and Embed Links in that channel.")
            await interaction.response.defer(ephemeral=True)
            await self.store.update(interaction.guild_id, log_channel=channel.id)
            delivered = await self.log_action(interaction.guild, "Log channel connected", f"Moderator {interaction.user.id} connected this channel.")
            await interaction.followup.send("Log channel saved and test log sent." if delivered else "Channel saved, but the test log failed. Check channel permissions.", ephemeral=True)

        @mod.command(name="warn", description="Record a warning and apply configured escalation")
        async def warn(interaction: discord.Interaction, member: discord.Member,
                       reason: app_commands.Range[str, 1, 300]):
            failure = target_error(interaction.user, member, interaction.guild.me)
            if failure:
                raise UserError(failure)
            await interaction.response.defer(ephemeral=True)
            async with self.member_lock(interaction.guild_id, member.id):
                value = await self.store.settings(interaction.guild_id)
                result = await self.issue_warning(interaction.guild, member, interaction.user.id,
                                                  reason, f"interaction:{interaction.id}", value)
            await interaction.followup.send(result or "This warning was already recorded.", ephemeral=True)

        @mod.command(name="warnings", description="View a member's 10 most recent warnings, including expired ones")
        async def warnings(interaction: discord.Interaction, member: discord.Member):
            rows = await self.store.warnings(interaction.guild_id, member.id)
            embed = discord.Embed(title=f"☕ Warnings · member {member.id}", color=COLOR)
            embed.description = "\n\n".join(
                f"**#{r['id']}** · <t:{int(r['created_at'])}:f> · moderator {r['moderator_id']}\n{safe(r['reason'], 300)}"
                for r in rows)[:4000] or "No warnings recorded."
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @mod.command(name="clearwarnings", description="Permanently delete a member's warnings; does not remove timeout")
        async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
            failure = target_error(interaction.user, member, interaction.guild.me)
            if failure:
                raise UserError(failure)
            await interaction.response.defer(ephemeral=True)
            async with self.member_lock(interaction.guild_id, member.id):
                count = await self.store.clear(interaction.guild_id, member.id)
            await self.log_action(interaction.guild, "Warnings cleared", f"Moderator {interaction.user.id} removed {count} warning(s) for member {member.id}.")
            await interaction.followup.send(f"Deleted {count} warning(s). Existing timeout unchanged.", ephemeral=True)

        @mod.command(name="timeout", description="Timeout a member (1 minute to 28 days); preserves longer timeouts")
        async def timeout(interaction: discord.Interaction, member: discord.Member,
                          minutes: app_commands.Range[int, 1, 40320], reason: app_commands.Range[str, 1, 300]):
            failure = target_error(interaction.user, member, interaction.guild.me)
            if failure:
                raise UserError(failure)
            await interaction.response.defer(ephemeral=True)
            async with self.member_lock(interaction.guild_id, member.id):
                result = await self.set_timeout(member, minutes, f"Staff {interaction.user.id}: {reason}")
            await self.log_action(interaction.guild, "Staff timeout", f"Moderator {interaction.user.id} · member {member.id}\n{result}")
            await interaction.followup.send(result, ephemeral=True)

        @mod.command(name="untimeout", description="Remove a member's existing timeout")
        async def untimeout(interaction: discord.Interaction, member: discord.Member,
                            reason: app_commands.Range[str, 1, 300]):
            failure = target_error(interaction.user, member, interaction.guild.me)
            if failure:
                raise UserError(failure)
            if not interaction.guild.me.guild_permissions.moderate_members:
                raise UserError("Espresso needs Moderate Members.")
            await interaction.response.defer(ephemeral=True)
            async with self.member_lock(interaction.guild_id, member.id):
                await member.timeout(None, reason=f"Staff {interaction.user.id}: {reason}"[:512])
            await self.log_action(interaction.guild, "Timeout removed", f"Moderator {interaction.user.id} · member {member.id}.")
            await interaction.followup.send("Timeout removed.", ephemeral=True)

        self.tree.add_command(config)
        self.tree.add_command(mod)
