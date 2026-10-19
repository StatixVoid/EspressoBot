"""Native events, persistent suggestion votes, and privacy-aware message highlights."""

from datetime import datetime, timezone
import time

import discord

from .hub_config import text, SUGGESTION_STATUSES


class SuggestionView(discord.ui.View):
    def __init__(self, manager, item_id, disabled=False):
        super().__init__(timeout=None)
        for label,value,style in (("Upvote",1,discord.ButtonStyle.success),("Downvote",-1,discord.ButtonStyle.secondary)):
            button = discord.ui.Button(label=label,custom_id=f"espresso:suggestion:{item_id}:{value}",style=style,disabled=disabled)
            async def vote(interaction,value=value):
                await interaction.response.defer(ephemeral=True)
                try:
                    counts = await manager.vote(interaction.guild,interaction.user.id,item_id,value)
                    await interaction.followup.send(f"Vote saved. {counts['up']} up · {counts['down']} down. Click the same choice to remove your vote.",ephemeral=True)
                except (ValueError,discord.HTTPException) as exc:
                    await interaction.followup.send(str(exc) if isinstance(exc,ValueError) else "The vote was saved, but the message update could not be confirmed. Refresh before voting again.",ephemeral=True)
            button.callback = vote
            self.add_item(button)


class SocialFeatures:
    async def create_suggestion(self, guild, user_id, idea, source):
        from .hub import NO_MENTIONS
        policy = await self.policy(guild.id,"suggestions")
        member = await self.member(guild,user_id)
        idea = text(idea,"Suggestion",1,1500)
        async with self.lock(guild.id):
            channel = await self.text_channel(guild,policy["suggestion_channel_id"],member)
            row,created = await self.store.begin_hub_item(guild.id,"suggestion",member.id,{"idea":idea,"note":""},source,channel.id)
            if not created:
                return row
            row["status"] = "open"
            try:
                message = await channel.send(embed=await self.suggestion_embed(row),view=SuggestionView(self,row["id"]),allowed_mentions=NO_MENTIONS)
            except discord.Forbidden:
                await self.store.update_hub_item(guild.id,row["id"],status="failed",data={"outcome":"Discord refused the suggestion post."})
                raise
            except discord.HTTPException:
                await self.store.update_hub_item(guild.id,row["id"],status="uncertain",data={"outcome":"Check the board before retiring this request. No automatic repost."})
                raise
            await self.store.update_hub_item(guild.id,row["id"],status="open",message_id=message.id)
            return await self.store.hub_item(guild.id,row["id"])

    async def suggestion_embed(self, row):
        from .hub import clean, COLOR
        counts = await self.store.suggestion_votes(row["guild_id"],row["id"])
        embed = discord.Embed(title=f"Suggestion #{row['id']} · {row['status'].replace('_',' ')}",description=clean(row["data"].get("idea",""),2000),color=COLOR)
        embed.add_field(name="Community votes",value=f"{counts['up']} up · {counts['down']} down")
        if row["data"].get("note"):
            embed.add_field(name="Staff update",value=clean(row["data"]["note"],1000),inline=False)
        embed.set_footer(text=f"Suggested by member {row['user_id']} · one vote per member")
        return embed

    async def refresh_suggestion(self, guild, row):
        from .hub import NO_MENTIONS
        channel = await self.text_channel(guild,row["channel_id"])
        message = await channel.fetch_message(row["message_id"])
        await message.edit(embed=await self.suggestion_embed(row),view=SuggestionView(self,row["id"],row["status"] in ("completed","declined","removed")),allowed_mentions=NO_MENTIONS)

    async def vote(self, guild, user_id, item_id, value):
        if guild is None:
            raise ValueError("Use suggestion controls in their server.")
        await self.policy(guild.id,"suggestions")
        member = await self.member(guild,user_id)
        async with self.lock(guild.id):
            row = await self.store.hub_item(guild.id,item_id)
            if not row or row["kind"] != "suggestion":
                raise ValueError("That suggestion no longer exists in this server.")
            channel = await self.text_channel(guild,row["channel_id"],member)
            if not channel.permissions_for(member).read_message_history:
                raise ValueError("You need access to the suggestion board.")
            counts = await self.store.vote_suggestion(guild.id,item_id,user_id,value)
            await self.refresh_suggestion(guild,row)
            return counts

    async def suggestion_status(self, guild, user_id, item_id, status, note, version=None):
        await self.manager(guild,user_id)
        if status not in SUGGESTION_STATUSES:
            raise ValueError("Choose a valid suggestion status.")
        note = text(note,"Public staff update",1,800)
        async with self.lock(guild.id):
            row = await self.store.hub_item(guild.id,item_id)
            if not row or row["kind"] != "suggestion" or row["status"] in ("posting","uncertain","failed","removed"):
                raise ValueError("This suggestion has no confirmed board post.")
            await self.store.update_hub_item(guild.id,item_id,version=version if version is not None else row["version"],status=status,data={"note":note,"reviewer_id":user_id})
            row = await self.store.hub_item(guild.id,item_id)
            await self.refresh_suggestion(guild,row)

    async def create_event(self, guild, user_id, name, description, location, start_at, end_at, source):
        policy = await self.policy(guild.id,"events")
        actor = await self.manager(guild,user_id)
        if not actor.guild_permissions.manage_events:
            raise ValueError("Creating events also requires Manage Events.")
        if guild.me is None or not (guild.me.guild_permissions.create_events or guild.me.guild_permissions.manage_events):
            raise ValueError("Espresso needs Create Events or Manage Events.")
        name,description,location = text(name,"Event name",1,100),text(description,"Description",0,1000),text(location,"Location",1,100)
        import math
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start_at,end_at)) or not time.time()+10 <= start_at <= time.time()+366*86400 or not start_at < end_at <= start_at+7*86400:
            raise ValueError("Start the event between 10 seconds and a year from now; end it within seven days of its start.")
        await self.community_role(guild,policy["event_role_id"],actor)
        data = {"name":name,"description":description,"location":location,"start_at":start_at,"end_at":end_at,"role_id":policy["event_role_id"]}
        async with self.lock(guild.id):
            row,created = await self.store.begin_hub_item(guild.id,"event",actor.id,data,source)
            if not created:
                return row
            try:
                event = await guild.create_scheduled_event(name=name,description=description,location=location,entity_type=discord.EntityType.external,
                    privacy_level=discord.PrivacyLevel.guild_only,start_time=datetime.fromtimestamp(start_at,timezone.utc),end_time=datetime.fromtimestamp(end_at,timezone.utc),reason=f"Espresso event requested by {actor.id}")
            except discord.Forbidden:
                await self.store.update_hub_item(guild.id,row["id"],status="failed",data={"outcome":"Discord refused event creation."})
                raise
            except discord.HTTPException:
                await self.store.update_hub_item(guild.id,row["id"],status="uncertain",data={"outcome":"Check Discord Events before retiring this request. No automatic retry."})
                raise
            await self.store.update_hub_item(guild.id,row["id"],status="scheduled",data={"event_id":event.id})
            return await self.store.hub_item(guild.id,row["id"])

    async def event_rsvp(self, guild, user_id, item_id, enabled):
        if type(enabled) is not bool:
            raise ValueError("Choose whether to receive event reminders.")
        await self.policy(guild.id,"events" if enabled else None)
        await self.member(guild,user_id,participate=enabled)
        async with self.lock(guild.id):
            row = await self.store.hub_item(guild.id,item_id)
            if not row or row["kind"] != "event" or (enabled and row["status"] not in ("scheduled","active")):
                raise ValueError("Choose an upcoming or active Espresso event.")
            if enabled:
                event = await guild.fetch_scheduled_event(row["data"]["event_id"])
                if event.status in (discord.EventStatus.completed,discord.EventStatus.cancelled):
                    raise ValueError("This event has ended.")
            if enabled and len(await self.store.rsvps(guild.id,item_id))>=250 and user_id not in await self.store.rsvps(guild.id,item_id):
                raise ValueError("This event has reached its 250 reminder subscriptions.")
            await self.store.set_rsvp(guild.id,item_id,user_id,enabled)
            if not enabled:
                for grant in await self.store.role_grants(guild.id,f"event:{item_id}"):
                    if grant["user_id"]==user_id:
                        await self.store.write_role_grant(guild.id,grant["purpose"],user_id,grant["role_id"],0,grant["status"])
                await self.cleanup_roles(guild,time.time())

    async def end_event(self, guild, user_id, item_id, version=None):
        actor = await self.manager(guild,user_id)
        if not actor.guild_permissions.manage_events:
            raise ValueError("Ending events also requires Manage Events.")
        async with self.lock(guild.id):
            row = await self.store.hub_item(guild.id,item_id)
            if not row or row["kind"] != "event" or row["status"] not in ("scheduled","active"):
                raise ValueError("Choose an upcoming or active Espresso event.")
            if version is not None and row["version"]!=version:
                from .store import SettingsConflict
                raise SettingsConflict("This event changed. Refresh before ending it.")
            event = await guild.fetch_scheduled_event(row["data"]["event_id"])
            if event.status not in (discord.EventStatus.completed,discord.EventStatus.cancelled):
                await event.edit(status=discord.EventStatus.completed if event.status==discord.EventStatus.active else discord.EventStatus.cancelled,reason=f"Espresso event ended by {actor.id}")
            await self.store.update_hub_item(guild.id,item_id,status="cancelled")
            await self.store.expire_event_roles(guild.id,item_id)
            await self.cleanup_roles(guild,time.time())

    async def tick_events(self, guild, now):
        from .hub import clean
        policy = await self.policy(guild.id)
        for row in await self.store.hub_items(guild.id,"event",active=True,limit=100):
            if row["status"] not in ("scheduled","active") or not row["data"].get("event_id"):
                continue
            try:
                event = await guild.fetch_scheduled_event(row["data"]["event_id"])
            except discord.NotFound:
                await self.store.update_hub_item(guild.id,row["id"],status="cancelled")
                await self.store.expire_event_roles(guild.id,row["id"])
                continue
            except discord.HTTPException:
                continue
            start_at = event.start_time.timestamp()
            end_at = event.end_time.timestamp() if event.end_time else row["data"]["end_at"]
            ended = event.status in (discord.EventStatus.completed,discord.EventStatus.cancelled) or now>=end_at
            status = "completed" if ended else "active" if event.status==discord.EventStatus.active else "scheduled"
            if status!=row["status"] or start_at!=row["data"]["start_at"] or end_at!=row["data"]["end_at"]:
                await self.store.update_hub_item(guild.id,row["id"],status=status,data={"start_at":start_at,"end_at":end_at,"name":event.name})
            if ended or not policy["events_enabled"]:
                await self.store.expire_event_roles(guild.id,row["id"])
                continue
            for user_id in await self.store.rsvps(guild.id,row["id"]):
                try:
                    member = await self.member(guild,user_id)
                except discord.NotFound:
                    await self.store.set_rsvp(guild.id,row["id"],user_id,False)
                    continue
                except (discord.HTTPException,ValueError):
                    continue
                if start_at-900 <= now < start_at:
                    source = f"event:{row['id']}:{int(start_at)}:{user_id}"
                    if await self.store.claim_hub_delivery(guild.id,source):
                        try:
                            await member.send(f"☕ {clean(event.name,150)} in {clean(guild.name,100)} starts <t:{int(start_at)}:R>.\nhttps://discord.com/events/{guild.id}/{event.id}\nUse /events reminders enabled:False in that server to unsubscribe.")
                            await self.store.finish_hub_delivery(guild.id,source,"sent")
                        except discord.Forbidden:
                            await self.store.finish_hub_delivery(guild.id,source,"failed","Member DMs are closed.")
                        except discord.HTTPException:
                            await self.store.finish_hub_delivery(guild.id,source,"uncertain","DM delivery is uncertain. No automatic resend.")
                if event.status==discord.EventStatus.active and start_at<=now<end_at and row["data"].get("role_id"):
                    try:
                        await self.grant_temporary_role(guild,member,row["data"]["role_id"],f"event:{row['id']}",end_at)
                    except (ValueError,discord.HTTPException):
                        continue

    async def highlight_reaction(self, payload):
        if payload.guild_id is None or str(payload.emoji).replace("\ufe0f","")!="⭐":
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        policy = await self.policy(guild.id)
        if payload.channel_id not in policy["highlight_source_ids"]:
            return
        async with self.lock(guild.id):
            await self.refresh_highlight(guild,payload.channel_id,payload.message_id)

    async def highlight_safe(self, guild, policy, channel_id, message_id):
        if not policy["highlights_enabled"] or channel_id not in policy["highlight_source_ids"]:
            return None
        try:
            source = await self.text_channel(guild,channel_id,public=True)
            target = await self.text_channel(guild,policy["highlight_channel_id"],public=True)
            if source.is_nsfw() and not target.is_nsfw():
                return None
            message = await source.fetch_message(message_id)
            if message.author.bot or message.webhook_id or await self.store.highlight_optout(guild.id,message.author.id):
                return None
            return message,target
        except (ValueError,discord.NotFound,discord.Forbidden):
            return None

    async def refresh_highlight(self, guild, channel_id, message_id):
        from .hub import clean, COLOR, NO_MENTIONS
        policy = await self.policy(guild.id)
        source_key = f"message:{message_id}"
        row = await self.store.hub_item_source(guild.id,"highlight",source_key)
        if row and row["data"].get("blocked"):
            return
        safe = await self.highlight_safe(guild,policy,channel_id,message_id)
        if safe is None:
            if row:
                await self.delete_highlight_post(guild,row)
            return
        message,target = safe
        stars = next((reaction for reaction in message.reactions if str(reaction.emoji).replace("\ufe0f","")=="⭐"),None)
        voters = set()
        if stars:
            async for user in stars.users(limit=100):
                if not user.bot and user.id!=message.author.id:
                    voters.add(user.id)
        if len(voters)<policy["highlight_threshold"]:
            if row:
                await self.delete_highlight_post(guild,row)
            return
        embed = discord.Embed(title=f"⭐ Community highlight · {len(voters)} stars",description=clean(message.content,700) or "A moment worth sharing.",color=COLOR)
        embed.add_field(name="Original message",value=f"[Open in Discord]({message.jump_url})",inline=False)
        embed.set_footer(text=f"Member {message.author.id} · /highlights optout to stop sharing your messages")
        if row and row["status"]=="active" and row["message_id"]:
            try:
                old_target = await guild.fetch_channel(row["channel_id"])
                post = await old_target.fetch_message(row["message_id"])
                if old_target.id != target.id:
                    await self.delete_highlight_post(guild,row)
                    return
                await post.edit(embed=embed,allowed_mentions=NO_MENTIONS)
            except discord.NotFound:
                await self.store.update_hub_item(guild.id,row["id"],status="removed")
            return
        if row:  # Removed, failed, and uncertain posts require explicit staff review; no blind repost.
            return
        data = {"source_channel_id":channel_id,"source_message_id":message_id,"blocked":False}
        row,created = await self.store.begin_hub_item(guild.id,"highlight",message.author.id,data,source_key,target.id)
        if not created:
            return
        try:
            post = await target.send(embed=embed,allowed_mentions=NO_MENTIONS)
        except discord.Forbidden:
            await self.store.update_hub_item(guild.id,row["id"],status="failed")
            return
        except discord.HTTPException:
            await self.store.update_hub_item(guild.id,row["id"],status="uncertain")
            return
        await self.store.update_hub_item(guild.id,row["id"],status="active",message_id=post.id)

    async def delete_highlight_post(self, guild, row, *, blocked=False):
        if blocked:
            await self.store.update_hub_item(guild.id,row["id"],data={"blocked":True})
        if row["status"] in ("posting","uncertain") and row["message_id"] is None:
            # Recover a lost acknowledgement by matching our own exact source link.
            # This also lets a member's opt-out remove an ambiguously delivered post.
            try:
                target = await guild.fetch_channel(row["channel_id"])
                original = f"https://discord.com/channels/{guild.id}/{row['data']['source_channel_id']}/{row['data']['source_message_id']}"
                matches = []
                async for message in target.history(limit=200):
                    if message.author.id==self.bot.user.id and any(original in field.value for embed in message.embeds for field in embed.fields):
                        matches.append(message)
                for message in matches:
                    await message.delete()
                if not matches:
                    await self.store.update_hub_item(guild.id,row["id"],data={"outcome":"Could not locate an uncertain post in the latest 200 board messages. Staff must check the board."})
                    return
            except discord.NotFound:
                pass
            except discord.HTTPException:
                return
        if row["message_id"] and row["status"]!="removed":
            try:
                channel = await guild.fetch_channel(row["channel_id"])
                await (await channel.fetch_message(row["message_id"])).delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                await self.store.update_hub_item(guild.id,row["id"],data={"outcome":"Removal needs retry; check board permissions."})
                return
        await self.store.update_hub_item(guild.id,row["id"],status="removed")

    async def set_highlight_optout(self, guild, user_id, enabled):
        await self.member(guild,user_id,participate=False)
        if type(enabled) is not bool:
            raise ValueError("Choose whether to opt out of highlights.")
        async with self.lock(guild.id):
            await self.store.highlight_optout(guild.id,user_id,enabled)
            if enabled:
                for row in await self.store.hub_items(guild.id,"highlight",user_id=user_id,active=True,limit=200):
                    await self.delete_highlight_post(guild,row)

    async def remove_highlight(self, guild, user_id, item_id):
        await self.manager(guild,user_id)
        async with self.lock(guild.id):
            row = await self.store.hub_item(guild.id,item_id)
            if not row or row["kind"]!="highlight":
                raise ValueError("Choose a highlight from this server.")
            await self.delete_highlight_post(guild,row,blocked=True)

    async def reconcile_highlights(self, guild, now):
        policy = await self.policy(guild.id)
        for row in await self.store.hub_items(guild.id,"highlight",active=True,limit=200):
            if row["status"] in ("posting","uncertain"):
                if not policy["highlights_enabled"] or row["data"].get("blocked") or await self.store.highlight_optout(guild.id,row["user_id"]):
                    await self.delete_highlight_post(guild,row)
                continue
            if row["status"]!="active":
                continue
            safe = None if row["data"].get("blocked") else await self.highlight_safe(guild,policy,row["data"]["source_channel_id"],row["data"]["source_message_id"])
            if safe is None or safe[1].id!=row["channel_id"]:
                await self.delete_highlight_post(guild,row)

    async def message_deleted(self, guild_id, message_id):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        async with self.lock(guild_id):
            for row in await self.store.hub_items(guild_id,"highlight",active=True,limit=200):
                if row["data"].get("source_message_id")==message_id:
                    await self.delete_highlight_post(guild,row)

    async def message_edited(self, message):
        if message.guild is None or message.author.bot:
            return
        rows = await self.store.hub_items(message.guild.id,"highlight",active=True,limit=200)
        if any(row["data"].get("source_message_id")==message.id for row in rows):
            async with self.lock(message.guild.id):
                await self.refresh_highlight(message.guild,message.channel.id,message.id)
