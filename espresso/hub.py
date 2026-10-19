"""Community hub orchestration, fresh access checks, and temporary role ownership."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import time
from zoneinfo import ZoneInfo

import discord

from .hub_config import validate_hub, THEMES
from .hub_tickets import TicketFeatures
from .hub_social import SocialFeatures
from .hub_voice import VoiceFeatures
from .reaction_roles import role_error

LOG = logging.getLogger("espresso")
COLOR = 0xD8A7B1
NO_MENTIONS = discord.AllowedMentions.none()


def clean(value, limit=1000):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


class HubManager(TicketFeatures, SocialFeatures, VoiceFeatures):
    def __init__(self, bot):
        self.bot, self.store = bot, bot.store
        self.locks = [asyncio.Lock() for _ in range(64)]
        self.task = None
        self.last_tick = 0

    def lock(self, guild_id):
        return self.locks[guild_id % len(self.locks)]

    async def member(self, guild, user_id, *, participate=True):
        member = await guild.fetch_member(user_id)
        if member.bot:
            raise ValueError("This feature is for community members.")
        if participate:
            if member.pending:
                raise ValueError("Complete Discord's member screening first.")
            settings = await self.store.settings(guild.id)
            if settings.screening["enabled"] and not (member.guild_permissions.manage_guild or member.guild_permissions.administrator):
                if settings.screening["verified_role_id"] not in {role.id for role in member.roles}:
                    raise ValueError("Complete /verify before using community features.")
        return member

    async def manager(self, guild, user_id):
        actor = await self.member(guild,user_id,participate=False)
        if not actor.guild_permissions.manage_guild:
            raise ValueError("You need Manage Server permission.")
        return actor

    async def policy(self, guild_id, feature=None):
        policy, _ = await self.store.hub_settings(guild_id)
        if feature and not policy[feature + "_enabled"]:
            raise ValueError(f"{feature.title()} is disabled. A manager can enable it in Community hub.")
        return policy

    async def text_channel(self, guild, channel_id, actor=None, *, public=False):
        channel = await guild.fetch_channel(channel_id)
        if not isinstance(channel,discord.TextChannel) or channel.guild.id != guild.id:
            raise ValueError("Choose a text channel from this server.")
        required = ("view_channel","send_messages","embed_links","read_message_history")
        if guild.me is None or not all(getattr(channel.permissions_for(guild.me),key) for key in required):
            raise ValueError("Espresso needs View Channel, Send Messages, Embed Links, and Read Message History.")
        if actor is not None and not channel.permissions_for(actor).view_channel:
            raise ValueError("Choose a channel you can view.")
        if public and not channel.permissions_for(guild.default_role).view_channel:
            raise ValueError("Highlight channels must be visible to @everyone.")
        return channel

    async def community_role(self, guild, role_id, actor=None):
        if role_id is None:
            return None
        roles = {role.id:role for role in await guild.fetch_roles()}
        role = roles.get(role_id)
        error = role_error(role,guild,guild.me,actor)
        if error:
            raise ValueError(error.replace("Reaction roles","Temporary roles").replace("reaction role","temporary role"))
        if any(getattr(role.permissions,flag,False) for flag in ("create_events","mute_members","deafen_members","move_members")):
            raise ValueError("Temporary community roles cannot grant event creation or voice moderation permissions.")
        settings = await self.store.settings(guild.id)
        occupied = {item["role_id"] for item in settings.reaction_roles}
        occupied.add(settings.screening["verified_role_id"])
        for menu in await self.store.menus(guild.id):
            if menu["status"] != "disabled":
                occupied.update(option["role_id"] for option in menu["data"].get("options",[]))
        if role_id in occupied:
            raise ValueError("Use a dedicated temporary role, separate from verification and role menus.")
        return role

    async def validate_settings(self, guild, actor_id, raw):
        actor = await self.manager(guild,actor_id)
        policy = validate_hub(raw)
        channels = {channel.id:channel for channel in await guild.fetch_channels()}
        for key,kind in (("ticket_category_id",discord.CategoryChannel),("voice_category_id",discord.CategoryChannel),("voice_lobby_id",discord.VoiceChannel)):
            if policy[key] is not None:
                channel = channels.get(policy[key])
                if not isinstance(channel,kind) or not channel.permissions_for(actor).view_channel:
                    raise ValueError(f"Choose an accessible server channel for {key.replace('_',' ')}.")
        if policy["tickets_enabled"]:
            category = channels[policy["ticket_category_id"]]
            if guild.me is None or not all(getattr(category.permissions_for(guild.me),p) for p in ("view_channel","manage_channels","manage_roles")):
                raise ValueError("Tickets need View Channel, Manage Channels, and Manage Roles in their category.")
            roles = {role.id:role for role in await guild.fetch_roles()}
            support = roles.get(policy["support_role_id"])
            if support is None or support.is_default() or support.managed:
                raise ValueError("Choose an existing support team role other than @everyone.")
        if policy["voice_enabled"]:
            lobby = channels[policy["voice_lobby_id"]]
            category = channels[policy["voice_category_id"]]
            if guild.me is None or not guild.me.guild_permissions.move_members or not all(getattr(category.permissions_for(guild.me),p) for p in ("view_channel","manage_channels","manage_roles","connect")) or not lobby.permissions_for(guild.me).view_channel:
                raise ValueError("Voice rooms need Move Members, plus View Channel, Connect, Manage Channels, and Manage Roles in their category.")
        for key,feature in (("suggestion_channel_id","suggestions"),("birthday_channel_id","birthdays"),("highlight_channel_id","highlights")):
            if policy[key] is not None:
                await self.text_channel(guild,policy[key],actor,public=feature=="highlights")
        for channel_id in policy["highlight_source_ids"]:
            await self.text_channel(guild,channel_id,actor,public=True)
        if policy["event_role_id"] is not None and policy["event_role_id"] == policy["birthday_role_id"]:
            raise ValueError("Use different event and birthday roles so their lifetimes stay independent.")
        for key in ("event_role_id","birthday_role_id"):
            await self.community_role(guild,policy[key],actor)
        return policy

    async def restore_views(self):
        from .hub_social import SuggestionView
        before = None
        while True:
            rows = await self.store.hub_items(kind="suggestion",active=True,before=before,limit=200)
            for row in rows:
                if row["message_id"] and row["status"] in ("open","under_review","accepted","planned"):
                    self.bot.add_view(SuggestionView(self,row["id"]),message_id=row["message_id"])
            if len(rows)<200:
                break
            before = rows[-1]["id"]

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.worker(),name="espresso-community-hub")

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def worker(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            if self.bot.is_ready():
                try:
                    await self.tick()
                except Exception as exc:
                    LOG.warning("Community hub work failed (%s).",type(exc).__name__)
            await asyncio.sleep(60)

    async def tick(self, now=None):
        now = time.time() if now is None else now
        for guild in self.bot.guilds:
            if guild.unavailable:
                continue
            async with self.lock(guild.id):
                for operation in (self.reconcile_tickets,self.cleanup_voice,self.tick_events,self.tick_birthdays,self.cleanup_roles,self.reconcile_highlights):
                    try:
                        await operation(guild,now)
                    except Exception as exc:
                        LOG.warning("Community hub %s failed: guild=%s (%s).",operation.__name__,guild.id,type(exc).__name__)
        await self.store.purge_hub_private_data(now)

    async def grant_temporary_role(self, guild, member, role_id, purpose, expires_at):
        role = await self.community_role(guild,role_id)
        if role is None:
            return
        async with self.bot.member_lock(guild.id,member.id):
            member = await self.member(guild,member.id)
            owned = next((row for row in await self.store.role_grants(guild.id,purpose) if row["user_id"]==member.id and row["role_id"]==role_id),None)
            present = role_id in {item.id for item in member.roles}
            if present and not owned:
                return  # A role already held is never claimed as Espresso's grant.
            if not present:
                await self.store.write_role_grant(guild.id,purpose,member.id,role_id,expires_at)
                await member.add_roles(role,reason="Espresso temporary community role",atomic=True)
            await self.store.write_role_grant(guild.id,purpose,member.id,role_id,expires_at,"applied")

    async def cleanup_roles(self, guild, now):
        grants = await self.store.role_grants(guild.id)
        for row in grants:
            if row["expires_at"] > now:
                continue
            try:
                async with self.bot.member_lock(guild.id,row["user_id"]):
                    member = await guild.fetch_member(row["user_id"])
                    role = next((r for r in await guild.fetch_roles() if r.id == row["role_id"]),None)
                    if role is not None and role.id in {r.id for r in member.roles}:
                        # Never remove a role that has since become privileged or a verification/menu role.
                        await self.community_role(guild,role.id)
                        overlaps = any(other["user_id"]==row["user_id"] and other["role_id"]==row["role_id"] and other["purpose"]!=row["purpose"] and other["expires_at"]>now for other in grants)
                        if not overlaps:
                            await member.remove_roles(role,reason="Espresso temporary role expired",atomic=True)
                    await self.store.forget_hub_role(row)
            except discord.NotFound:
                await self.store.forget_hub_role(row)
            except (discord.HTTPException,ValueError):
                continue

    async def tick_birthdays(self, guild, now):
        policy = await self.policy(guild.id)
        if not policy["birthdays_enabled"]:
            return
        local = datetime.fromtimestamp(now,ZoneInfo(policy["timezone"]))
        if local.hour < policy["birthday_hour"]:
            return
        channel = await self.text_channel(guild,policy["birthday_channel_id"])
        tomorrow = datetime.combine(local.date()+timedelta(days=1),datetime.min.time(),ZoneInfo(policy["timezone"])).timestamp()
        for row in (await self.store.birthdays_on(guild.id,local.date()))[:100]:
            try:
                member = await self.member(guild,row["user_id"])
            except discord.NotFound:
                await self.store.birthday(guild.id,row["user_id"])
                continue
            except (discord.HTTPException,ValueError):
                continue
            if not channel.permissions_for(member).view_channel:
                continue
            source = f"birthday:{local.date()}:{member.id}"
            if await self.store.claim_hub_delivery(guild.id,source):
                try:
                    await channel.send(embed=discord.Embed(title="A birthday at the café 🎂",description=f"Happy birthday, {clean(member.display_name,100)}! Wishing you a lovely day.",color=COLOR),allowed_mentions=NO_MENTIONS)
                    await self.store.finish_hub_delivery(guild.id,source,"sent")
                except discord.Forbidden:
                    await self.store.finish_hub_delivery(guild.id,source,"failed","Birthday channel permissions changed.")
                except discord.HTTPException:
                    await self.store.finish_hub_delivery(guild.id,source,"uncertain","Check the birthday channel. No automatic resend.")
            if policy["birthday_role_id"] is not None:
                try:
                    await self.grant_temporary_role(guild,member,policy["birthday_role_id"],source,tomorrow)
                except (discord.HTTPException,ValueError):
                    continue

    async def profile_embed(self, guild, member):
        await self.policy(guild.id,"profiles")
        profile = await self.store.profile(guild.id,member.id)
        if profile is None:
            raise ValueError("This member has not created a café profile. Use /profile edit to opt in.")
        embed = discord.Embed(title=f"{clean(member.display_name,80)}'s café card",description=clean(profile["bio"],300) or "A familiar face at the café.",color=THEMES[profile["theme"]])
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Community thanks",value=str(profile["thanks"]))
        embed.add_field(name="Badges",value=" · ".join(clean(badge,60) for badge in profile["badges"]) or "Your story is just beginning.",inline=False)
        return embed
