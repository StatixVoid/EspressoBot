"""Community features and recoverable, bounded background work."""

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone, timedelta
import hashlib
import logging
import random
import time

import discord

from .community_config import channel_settings
from .rules import content_decision, normalize

LOG = logging.getLogger("espresso")
COLOR = 0xD8A7B1
PROMPTS = (
    "What's one small thing that made your day better?",
    "What song belongs on the café playlist today?",
    "What's your go-to coffee shop order?",
    "What are you looking forward to this week?",
    "Share a book, game, or show you've enjoyed lately.",
    "If this server had a signature drink, what would it be?",
    "What's a hobby you'd love to try?",
    "What would your perfect day off look like?",
    "What's something you learned recently?",
    "What's your favorite way to unwind?",
    "What's a small win you'd like to celebrate?",
    "Which fictional place would you visit for an afternoon?",
)


def staff(actor):
    if not isinstance(actor, discord.Member) or not actor.guild_permissions.moderate_members:
        raise ValueError("You need Moderate Members permission.")


def manager(actor):
    if not isinstance(actor, discord.Member) or not actor.guild_permissions.manage_guild:
        raise ValueError("You need Manage Server permission.")


def readable_ids(row):
    return {key: str(value) if value is not None and (key == "id" or key.endswith("_id")) else value for key,value in row.items()}


class CommunityManager:
    def __init__(self, bot):
        self.bot, self.store = bot, bot.store
        self.task = None
        self.locks = [asyncio.Lock() for _ in range(64)]
        self.joins, self.repeats, self.cooldowns = OrderedDict(), OrderedDict(), OrderedDict()
        self.command_cooldowns = OrderedDict()

    def guild_lock(self, guild_id):
        return self.locks[guild_id % len(self.locks)]

    def forget_guild(self, guild_id):
        self.joins.pop(guild_id,None)
        self.cooldowns.pop(guild_id,None)
        for mapping in (self.repeats,self.command_cooldowns):
            for key in list(mapping):
                if key[0] == guild_id:
                    del mapping[key]

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.worker(), name="espresso-community")

    async def close(self):
        if self.task is not None:
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
                    LOG.error("Community background work failed (%s).", type(exc).__name__)
            await asyncio.sleep(15)

    async def tick(self, now=None):
        now = time.time() if now is None else now
        for row in await self.store.recovery():
            if row["restore_at"] <= now:
                guild = self.bot.get_guild(row["guild_id"])
                if guild and not guild.unavailable:
                    async with self.guild_lock(guild.id):
                        await self.restore_channel(guild, row)
        for row in await self.store.claim_due_posts(now):
            await self.deliver_post(row, now)
        for guild in self.bot.guilds:
            try:
                settings = await self.store.settings(guild.id)
                policy = settings.community
                if not policy["prompts_enabled"] or guild.unavailable:
                    continue
                today = datetime.fromtimestamp(now, timezone.utc)
                key = today.date().isoformat()
                if today.hour < policy["prompt_hour_utc"] or await self.store.state(guild.id,"daily_prompt") == key:
                    continue
                # Use the same persistent delivery queue; restart and missed-day behavior are explicit.
                await self.store.schedule_post(guild.id,policy["prompt_channel_id"],self.bot.user.id,
                    "☕ Today's conversation starter: " + (policy["prompt_library"] or PROMPTS)[today.date().toordinal() % len(policy["prompt_library"] or PROMPTS)],
                    now+11,0,False,"prompt:"+key,now=now)
                await self.store.set_state(guild.id,"daily_prompt",key)
            except Exception as exc:
                LOG.warning("Daily prompt could not be queued: guild=%s (%s).", guild.id,type(exc).__name__)

    async def post_channel(self, guild, channel_id):
        channel = await guild.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Choose a text channel in this server.")
        me = guild.me
        if me is None or not all(getattr(channel.permissions_for(me),key) for key in ("view_channel","send_messages","embed_links")):
            raise ValueError("Espresso needs View Channel, Send Messages, and Embed Links in that channel.")
        return channel

    async def validate_settings(self, guild, actor, settings):
        manager(actor)
        if (settings.raid["enabled"] or settings.welcome["enabled"]) and not self.bot.intents.members:
            raise ValueError("Enable member events on the host before enabling join protection or welcome messages.")
        channels = {c.id:c for c in await guild.fetch_channels()}
        if any(rule["channel_id"] not in channels for rule in settings.channel_rules):
            raise ValueError("A channel rule refers to a channel outside this server.")
        for key,kind in (("excluded_channel_ids",discord.TextChannel),("excluded_category_ids",discord.CategoryChannel)):
            if any(item not in channels or not isinstance(channels[item],kind) for item in settings.raid[key]):
                raise ValueError("Choose raid exclusions from this server's text channels and categories.")
        for policy, key, enabled in ((settings.welcome,"channel_id",settings.welcome["enabled"]),
                                     (settings.community,"prompt_channel_id",settings.community["prompts_enabled"])):
            if enabled:
                channel = await self.post_channel(guild,policy[key])
                if not channel.permissions_for(actor).view_channel:
                    raise ValueError("Choose a channel you can view.")
        role_id = settings.community["notification_role_id"]
        if role_id is not None:
            role = next((r for r in await guild.fetch_roles() if r.id == role_id),None)
            if role is None or role.is_default() or role.managed or not role.mentionable:
                raise ValueError("Choose an existing, mentionable notification role other than @everyone.")
            if any(getattr(role.permissions,p) for p in ("administrator","manage_guild","manage_roles","manage_channels","kick_members","ban_members","moderate_members","mention_everyone")):
                raise ValueError("Choose an ordinary community notification role.")

    async def joined(self, member):
        if member.bot:
            return
        settings = await self.store.settings(member.guild.id)
        policy = settings.raid
        if policy["enabled"]:
            now = time.monotonic()
            bucket = self.joins.pop(member.guild.id,deque(maxlen=100))
            while bucket and bucket[0][0] <= now-policy["window_seconds"]:
                bucket.popleft()
            if not any(user_id == member.id for _,user_id in bucket):
                bucket.append((now,member.id))
            self.joins[member.guild.id] = bucket
            self.bound(self.joins,1000)
            if len(bucket) >= policy["join_limit"]:
                await self.raid_response(member.guild,settings,"Sudden join spike")
        if settings.welcome["enabled"]:
            try:
                channel = await self.post_channel(member.guild,settings.welcome["channel_id"])
                text = settings.welcome["message"].replace("{member}",member.mention).replace("{server}",discord.utils.escape_mentions(member.guild.name))
                embed = discord.Embed(title="A fresh face at the café ☕",description=text[:4000],color=COLOR)
                embed.set_thumbnail(url=member.display_avatar.url)
                await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
            except (ValueError,discord.HTTPException):
                await self.bot.log_action(member.guild,"Welcome delivery failed",f"Member {member.id}; check the welcome channel and permissions.")

    @staticmethod
    def bound(mapping, capacity):
        while len(mapping) > capacity:
            mapping.popitem(last=False)

    async def message(self, message, settings):
        if not settings.raid["enabled"] or len(normalize(message.content).strip()) < 8:
            return
        now = time.monotonic()
        digest = hashlib.sha256(normalize(message.content).strip().encode()).digest()
        key = (message.guild.id,digest)
        bucket = self.repeats.pop(key,deque(maxlen=100))
        while bucket and bucket[0][0] <= now-settings.raid["window_seconds"]:
            bucket.popleft()
        bucket.append((now,message.author.id))
        self.repeats[key] = bucket
        self.bound(self.repeats,5000)
        if len({user_id for _,user_id in bucket}) >= settings.raid["repeat_limit"]:
            await self.raid_response(message.guild,settings,"Repeated text from multiple members",message.channel)

    async def raid_response(self, guild, settings, reason, channel=None):
        async with self.guild_lock(guild.id):
            now = time.monotonic()
            if now-self.cooldowns.get(guild.id,-10000) < settings.raid["duration_minutes"]*60:
                return
            self.cooldowns[guild.id] = now
            self.cooldowns.move_to_end(guild.id)
            self.bound(self.cooldowns,1000)
            action = "alert" if settings.observation_mode else settings.raid["action"]
            changed,failed = 0,0
            if action != "alert":
                try:
                    if channel is None:
                        channels = await guild.fetch_channels()
                    elif isinstance(channel,discord.Thread):
                        channels = [await guild.fetch_channel(channel.parent_id)]
                    else:
                        channels = [channel]
                except discord.HTTPException:
                    channels,failed = [],1
                for candidate in channels:
                    if not isinstance(candidate,discord.TextChannel):
                        continue
                    try:
                        await self.protect_channel(guild,candidate.id,action,settings.raid)
                        changed += 1
                    except (ValueError,discord.HTTPException):
                        failed += 1
            case_id = await self.store.record_case(guild.id,None,self.bot.user.id,"raid",reason,f"{action}; channels protected {changed}; failures {failed}",f"raid:{time.time_ns()}")
            await self.bot.log_action(guild,"Raid alert",f"Case #{case_id} · {reason}. Action: {action}. Channels protected: {changed}; skipped/failed: {failed}." + (" Observation mode is enabled." if settings.observation_mode else ""))

    async def protect_channel(self, guild, channel_id, action, policy):
        channel = await guild.fetch_channel(channel_id)
        if not isinstance(channel,discord.TextChannel) or guild.me is None:
            raise ValueError("Channel is unavailable.")
        if channel.id in policy["excluded_channel_ids"] or channel.category_id in policy["excluded_category_ids"]:
            raise ValueError("This channel is excluded from raid restrictions.")
        if any(row["channel_id"] == channel_id for row in await self.store.recovery(guild.id)):
            raise ValueError("This channel already has protection awaiting restoration.")
        perms = channel.permissions_for(guild.me)
        if not perms.view_channel or not perms.send_messages:
            raise ValueError("Skip channels Espresso cannot participate in.")
        data = {"action":action,"old":{},"applied":{},"error":""}
        if action == "slowmode":
            if not perms.manage_channels:
                raise ValueError("Espresso needs Manage Channels for slowmode.")
            data["old"] = {"slowmode_delay":channel.slowmode_delay}
            data["applied"] = {"slowmode_delay":max(channel.slowmode_delay,policy["slowmode_seconds"])}
        elif action == "lockdown":
            if not perms.manage_roles:
                raise ValueError("Espresso needs Manage Roles to edit channel permissions.")
            overwrite = channel.overwrites_for(guild.default_role)
            for key in ("send_messages","send_messages_in_threads","create_public_threads","create_private_threads"):
                data["old"][key] = getattr(overwrite,key)
                data["applied"][key] = False
        else:
            raise ValueError("Choose slowmode or lockdown.")
        # Persist restoration intent before any Discord write, including ambiguous network outcomes.
        restore_at = time.time()+policy["duration_minutes"]*60
        await self.store.save_recovery(guild.id,channel_id,data,restore_at)
        if action == "slowmode":
            await channel.edit(slowmode_delay=data["applied"]["slowmode_delay"],reason="Espresso temporary raid protection")
        else:
            for key,value in data["applied"].items():
                setattr(overwrite,key,value)
            await channel.set_permissions(guild.default_role,overwrite=overwrite,reason="Espresso temporary raid protection")

    async def restore_channel(self, guild, row):
        data = row["data"]
        try:
            channel = await guild.fetch_channel(row["channel_id"])
            if data["action"] == "slowmode":
                if channel.slowmode_delay == data["applied"]["slowmode_delay"]:
                    await channel.edit(slowmode_delay=data["old"]["slowmode_delay"],reason="Espresso raid protection expired")
            else:
                overwrite = channel.overwrites_for(guild.default_role)
                changed = False
                for key,value in data["applied"].items():
                    if getattr(overwrite,key) == value:
                        setattr(overwrite,key,data["old"][key])
                        changed = True
                if changed:
                    await channel.set_permissions(guild.default_role,overwrite=None if overwrite.is_empty() else overwrite,reason="Espresso raid protection expired")
            await self.store.forget_recovery(guild.id,row["channel_id"])
            await self.bot.log_action(guild,"Raid protection ended",f"Channel {row['channel_id']} restored; subsequent staff changes were preserved.")
            return True
        except discord.NotFound:
            await self.store.forget_recovery(guild.id,row["channel_id"])
            return True
        except discord.HTTPException:
            data["error"] = "Restoration failed. Restore permissions or use End protection to retry."
            await self.store.save_recovery(guild.id,row["channel_id"],data,time.time()+60)
            if not row["data"].get("reported"):
                data["reported"] = True
                await self.store.save_recovery(guild.id,row["channel_id"],data,time.time()+60)
                await self.bot.log_action(guild,"Raid restoration needs attention",f"Channel {row['channel_id']}; check Manage Channels or Manage Roles. Recovery will retry.")
            return False

    async def end_protection(self, guild, actor):
        manager(actor)
        async with self.guild_lock(guild.id):
            results = [await self.restore_channel(guild,row) for row in await self.store.recovery(guild.id)]
        return {"restored":sum(results),"remaining":len(results)-sum(results)}

    async def remove_warning(self, guild, actor, warning_id, reason):
        from .bot import target_error
        staff(actor)
        row = await self.store.warning(guild.id,warning_id)
        if row is None:
            raise ValueError("That warning is not in this server.")
        try:
            member = await guild.fetch_member(row["user_id"])
        except discord.NotFound:
            if not actor.guild_permissions.manage_guild:
                raise ValueError("Manage Server is required to remove warnings for departed members.") from None
        else:
            failure = target_error(actor,member,guild.me)
            if failure:
                raise ValueError(failure)
        async with self.bot.member_lock(guild.id,row["user_id"]):
            changed = await self.store.void_warning(guild.id,warning_id,actor.id,reason)
            if changed:
                case_id = await self.store.record_case(guild.id,row["user_id"],actor.id,"warning_removed",reason,f"Warning #{warning_id} removed from active history; timeout unchanged.",f"void:{warning_id}")
                await self.bot.log_action(guild,"Warning removed",f"Case #{case_id} · warning {warning_id} · moderator {actor.id} · member {row['user_id']}.")
        return changed

    async def review(self, guild, actor, item_id, version, action, note, public_response="", notify=False):
        staff(actor)
        status = await self.store.review_inbox(guild.id,item_id,actor.id,version,action,note,public_response,notify)
        if notify:
            row = await self.store.inbox_item(guild.id,item_id)
            try:
                recipient = await self.bot.fetch_user(row["author_id"])
                text = f"☕ Your {row['kind']} #{item_id} in {discord.utils.escape_mentions(guild.name)} is {status}."
                if public_response:
                    text += "\n"+discord.utils.escape_mentions(public_response)
                await recipient.send(text,allowed_mentions=discord.AllowedMentions.none())
                notice_status = "sent"
            except discord.Forbidden:
                notice_status = "closed_dm"
            except discord.HTTPException:
                notice_status = "failed"
            except Exception:
                notice_status = "uncertain"
            await self.store.notice_result(guild.id,item_id,version+1,notice_status)
        await self.bot.log_action(guild,"Staff inbox updated",f"Item #{item_id} · moderator {actor.id} · {status}.")
        return status

    async def schedule(self, guild, actor, channel_id, content, due_at, repeat_days, notify, source, calendar=None):
        manager(actor)
        channel = await self.post_channel(guild,channel_id)
        if not channel.permissions_for(actor).view_channel or not channel.permissions_for(actor).send_messages:
            raise ValueError("You must be able to view and post in the selected channel.")
        settings = await self.store.settings(guild.id)
        if notify and settings.community["notification_role_id"] is None:
            raise ValueError("Choose a notification role in settings first.")
        post_id = await self.store.schedule_post(guild.id,channel_id,actor.id,content,due_at,repeat_days,notify,source,calendar=calendar)
        await self.bot.log_action(guild,"Post scheduled",f"Post #{post_id} · channel {channel_id} · moderator {actor.id}.")
        return post_id

    async def deliver_post(self, row, now):
        guild = self.bot.get_guild(row["guild_id"])
        try:
            if guild is None or guild.unavailable:
                raise ValueError("Server is unavailable; reschedule when Espresso is connected.")
            # Recheck the human author's current server and channel permissions at delivery time.
            channel = await self.post_channel(guild,row["channel_id"])
            if row["actor_id"] != self.bot.user.id:
                actor = await guild.fetch_member(row["actor_id"])
                manager(actor)
                if not channel.permissions_for(actor).view_channel or not channel.permissions_for(actor).send_messages:
                    raise ValueError("The author's channel permissions changed.")
            settings = await self.store.settings(guild.id)
            if row["source"].startswith("prompt:") and (not settings.community["prompts_enabled"] or settings.community["prompt_channel_id"] != row["channel_id"]):
                await self.store.finish_post(row,"cancelled","Daily prompts were disabled or moved before delivery.",now=now)
                return
            content,mentions = row["content"],discord.AllowedMentions.none()
            if row["notify"]:
                role_id = settings.community["notification_role_id"]
                role = next((role for role in await guild.fetch_roles() if role.id == role_id),None)
                if role is None or role.is_default() or not role.mentionable:
                    raise ValueError("The configured notification role is missing or no longer mentionable.")
                if role.managed or any(getattr(role.permissions,p) for p in ("administrator","manage_guild","manage_roles","manage_channels","kick_members","ban_members","moderate_members","mention_everyone")):
                    raise ValueError("The notification role is no longer an ordinary community role.")
                content = f"<@&{role_id}>\n"+content
                mentions = discord.AllowedMentions(everyone=False,users=False,roles=[role],replied_user=False)
            sent = await channel.send(content,allowed_mentions=mentions)
        except ValueError as exc:
            await self.store.finish_post(row,"failed",str(exc),now=now)
        except discord.HTTPException as exc:
            # A timeout/5xx might have accepted the message. Surface uncertainty instead of duplicating it.
            status = "uncertain" if exc.status >= 500 else "failed"
            await self.store.finish_post(row,status,f"Discord HTTP {exc.status}; check the channel before rescheduling.",now=now)
        except Exception as exc:
            await self.store.finish_post(row,"uncertain",f"Delivery could not be confirmed ({type(exc).__name__}); check Discord before rescheduling.",now=now)
        else:
            await self.store.finish_post(row,"sent","Delivered",sent.id,now=now)
        if guild:
            try:
                await self.bot.log_action(guild,"Scheduled post processed",f"Post #{row['id']} · channel {row['channel_id']}. Check Scheduled posts for the delivery result.")
            except Exception as exc:
                LOG.warning("Post outcome saved but activity delivery failed (%s).",type(exc).__name__)

    async def diagnostics(self, guild):
        settings = await self.store.settings(guild.id)
        checks = []
        def add(name, ok, fix):
            checks.append({"name":name,"ok":bool(ok),"detail":"Ready" if ok else fix})
        me = guild.me
        add("Bot connection",self.bot.is_ready(),"Wait for Espresso to reconnect.")
        for name,label in (("manage_messages","Delete filtered messages"),("moderate_members","Apply timeouts")):
            add(label,me and getattr(me.guild_permissions,name),f"Grant Espresso {name.replace('_',' ').title()}.")
        add("Read message content",self.bot.intents.message_content,"Enable Message Content Intent in Discord and restart Espresso.")
        needs_members = settings.screening["enabled"] or settings.welcome["enabled"] or settings.raid["enabled"]
        if needs_members:
            add("Receive member joins",self.bot.intents.members,"Enable Server Members Intent and ENABLE_MEMBER_SCREENING_EVENTS on the host.")
        channels = {channel.id:channel for channel in await guild.fetch_channels()}
        for label,channel_id in (("Staff log",settings.log_channel),("Welcome",settings.welcome["channel_id"] if settings.welcome["enabled"] else None),
                                 ("Daily prompts",settings.community["prompt_channel_id"] if settings.community["prompts_enabled"] else None),
                                 ("Verification entry",settings.screening["entry_channel_id"] if settings.screening["enabled"] else None)):
            if channel_id:
                channel = channels.get(channel_id)
                add(label+" channel",channel and me and all(getattr(channel.permissions_for(me),p) for p in ("view_channel","send_messages","embed_links")),"Choose an accessible text channel and allow View Channel, Send Messages, and Embed Links.")
        roles = await guild.fetch_roles()
        if settings.reaction_roles_enabled or settings.screening["enabled"]:
            add("Manage member roles",me and me.guild_permissions.manage_roles,"Grant Espresso Manage Roles.")
            role_ids = {item["role_id"] for item in settings.reaction_roles} if settings.reaction_roles_enabled else set()
            if settings.screening["enabled"]:
                role_ids.add(settings.screening["verified_role_id"])
            for role_id in sorted(role_ids):
                role = next((role for role in roles if role.id == role_id),None)
                add(f"Role {role.name if role else role_id}",role and me and me.top_role > role and not role.managed,"Move Espresso above this role; choose an existing role that another app does not manage.")
        if settings.screening["enabled"]:
            role = next((r for r in roles if r.id == settings.screening["verified_role_id"]),None)
            protected = 0
            for channel in channels.values():
                if isinstance(channel,discord.TextChannel) and role and not channel.permissions_for(guild.default_role).view_channel and channel.permissions_for(role).view_channel:
                    protected += 1
            add("Verification access gating",protected>0,"Hide protected categories from @everyone and allow the Verified role to view them. Check other roles for access bypasses.")
        recoveries = await self.store.recovery(guild.id)
        for row in recoveries:
            add(f"Restore channel {row['channel_id']}",not row["data"].get("error"),row["data"].get("error", ""))
        if settings.raid["enabled"] and settings.raid["action"] != "alert":
            perm = "manage_channels" if settings.raid["action"] == "slowmode" else "manage_roles"
            add("Raid response permission",me and getattr(me.guild_permissions,perm),f"Grant Espresso {perm.replace('_',' ').title()} and check channel overrides.")
        if settings.community["polls_enabled"]:
            add("Send community polls",me and me.guild_permissions.send_polls,"Grant Espresso Send Polls and check channel overrides.")
        return checks

    async def test_filter(self, guild_id, text, channel_id=None, parent_id=None):
        if type(text) is not str or len(text) > 4000:
            raise ValueError("Test up to 4000 characters.")
        settings = await self.store.settings(guild_id)
        effective = channel_settings(settings,channel_id,parent_id)
        decision = content_decision(text,effective)
        return {"matched":decision is not None,"rule":decision.reason if decision else None,
                "action":decision.action if decision else None,"moderation_enabled":effective.enabled,
                "observation_mode":effective.observation_mode,
                "note":"This tests text rules only. Spam requires message history; role exemptions, warning cooldowns, escalation, and Discord permissions also affect live behavior."}

    async def extend_protection(self, guild, actor, channel_id, minutes):
        manager(actor)
        async with self.guild_lock(guild.id):
            await self.store.extend_protection(guild.id,channel_id,minutes)
        await self.bot.log_action(guild,"Raid protection extended",f"Channel {channel_id} · moderator {actor.id} · {minutes} minutes (maximum one hour from now).")

    async def end_channel_protection(self, guild, actor, channel_id):
        manager(actor)
        async with self.guild_lock(guild.id):
            row = next((row for row in await self.store.recovery(guild.id) if row["channel_id"] == channel_id),None)
            if row is None:
                raise ValueError("This channel no longer has temporary protection.")
            return await self.restore_channel(guild,row)

    def command_limit(self, guild_id, user_id, kind, seconds):
        key,now = (guild_id,user_id,kind),time.monotonic()
        if now-self.command_cooldowns.get(key,-10000) < seconds:
            raise ValueError(f"Please wait {seconds} seconds between {kind} commands.")
        self.command_cooldowns[key] = now
        self.command_cooldowns.move_to_end(key)
        self.bound(self.command_cooldowns,10000)

    async def coffee(self, interaction, member):
        self.require_public_post(interaction)
        settings = await self.store.settings(interaction.guild_id)
        if not settings.community["coffee_enabled"]:
            raise ValueError("Coffee cards are off. Ask an admin to enable them in Community settings.")
        self.command_limit(interaction.guild_id,interaction.user.id,"coffee",30)
        drink = random.choice(("a caramel latte","a vanilla cold brew","a cappuccino","a cozy hot chocolate","an espresso"))
        policy = settings.community
        colors = {"classic":COLOR,"pastel":0xC9B5EB,"midnight":0x41314D,"custom":int(policy["coffee_color"],16)}
        text = policy["coffee_message"].replace("{sender}",interaction.user.mention).replace("{member}",member.mention).replace("{drink}",drink)
        embed = discord.Embed(title=policy["coffee_title"],description=text,color=colors[policy["coffee_style"]])
        embed.set_footer(text=f"Freshly brewed · {policy['coffee_style']} blend")
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed,allowed_mentions=discord.AllowedMentions.none())

    async def poll(self, interaction, question, options, hours):
        self.require_public_post(interaction,poll=True)
        settings = await self.store.settings(interaction.guild_id)
        if not settings.community["polls_enabled"]:
            raise ValueError("Community polls are off. Ask an admin to enable them in Community settings.")
        answers = [answer.strip() for answer in options.split("|")]
        if not 2 <= len(answers) <= 10 or len(set(answer.casefold() for answer in answers)) != len(answers) or any(not 1 <= len(answer) <= 55 for answer in answers):
            raise ValueError("Give 2–10 unique options, separated by |, each 1–55 characters.")
        self.command_limit(interaction.guild_id,interaction.user.id,"poll",120)
        poll = discord.Poll(question=question,duration=timedelta(hours=hours))
        for answer in answers:
            poll.add_answer(text=answer)
        await interaction.response.send_message(poll=poll,allowed_mentions=discord.AllowedMentions.none())

    @staticmethod
    def require_public_post(interaction, poll=False):
        if not interaction.guild or not isinstance(interaction.user,discord.Member) or interaction.channel is None:
            raise ValueError("Use this command in a server channel.")
        channel = interaction.channel
        for member in (interaction.user,interaction.guild.me):
            if member is None:
                raise ValueError("Espresso is reconnecting.")
            permissions = channel.permissions_for(member)
            send = permissions.send_messages_in_threads if isinstance(channel,discord.Thread) else permissions.send_messages
            if not permissions.view_channel or not send or (poll and not permissions.send_polls):
                raise ValueError("You and Espresso need permission to post here" + (" and send polls." if poll else "."))
