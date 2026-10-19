"""Private support rooms with explicit staff access and bounded text transcripts."""

import io
import time

import discord

from .hub_config import text


class TicketFeatures:
    async def open_ticket(self, guild, user_id, category, subject, source, *, save_transcript=False):
        from .hub import NO_MENTIONS, clean, COLOR
        policy = await self.policy(guild.id,"tickets")
        member = await self.member(guild,user_id)
        if category not in policy["ticket_categories"]:
            raise ValueError("Choose a configured support category.")
        subject = text(subject,"Subject",1,200)
        if type(save_transcript) is not bool:
            raise ValueError("Choose whether to retain a transcript for this ticket.")
        if save_transcript and not policy["ticket_transcripts"]:
            raise ValueError("Transcript storage is disabled by this server. Open the ticket without requesting a transcript.")
        async with self.lock(guild.id):
            parent = await guild.fetch_channel(policy["ticket_category_id"])
            if not isinstance(parent,discord.CategoryChannel):
                raise ValueError("The support category no longer exists.")
            support = next((r for r in await guild.fetch_roles() if r.id==policy["support_role_id"]),None)
            if support is None or support.is_default() or support.managed:
                raise ValueError("The support team role needs to be configured again.")
            me = guild.me
            if me is None or not all(getattr(parent.permissions_for(me),p) for p in ("view_channel","manage_channels","manage_roles")):
                raise ValueError("Espresso needs View Channel, Manage Channels, and Manage Roles in the support category.")
            data = {"category":category,"subject":subject,"transcripts":save_transcript,"assigned_id":None,"close_reason":""}
            row,created = await self.store.begin_hub_item(guild.id,"ticket",member.id,data,source)
            if not created:
                return row
            overwrites = {
                guild.default_role:discord.PermissionOverwrite(view_channel=False),
                support:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),
                member:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True),
                me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True,attach_files=True,embed_links=True),
            }
            try:
                channel = await guild.create_text_channel(f"ticket-{row['id']}",category=parent,overwrites=overwrites,topic=f"espresso-ticket:{row['id']}",reason="Espresso private support ticket")
            except discord.Forbidden:
                await self.store.update_hub_item(guild.id,row["id"],status="failed",data={"outcome":"Discord refused room creation. Check category permissions."})
                raise
            except discord.HTTPException:
                await self.store.update_hub_item(guild.id,row["id"],status="uncertain",data={"outcome":"Room creation is uncertain. Staff should check the category before clearing this request."})
                raise
            await self.store.update_hub_item(guild.id,row["id"],status="open",channel_id=channel.id)
            description = f"**{clean(category,80)}**\n{clean(subject,300)}\n\nUse `/ticket claim` to claim this room and `/ticket close` with a reason to close it."
            description += "\nA text transcript of up to 1,000 messages will be retained for 30 days after closing." if data["transcripts"] else "\nTranscript storage is disabled for this ticket."
            try:
                await channel.send(embed=discord.Embed(title=f"Support ticket #{row['id']}",description=description,color=COLOR),allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                await self.store.update_hub_item(guild.id,row["id"],data={"outcome":"Room created; the welcome message could not be confirmed."})
            return await self.store.hub_item(guild.id,row["id"])

    async def ticket_access(self, guild, user_id, row, *, staff_only=False):
        if row is None or row["kind"] != "ticket":
            raise ValueError("That support ticket is not in this server.")
        member = await self.member(guild,user_id,participate=False)
        policy = await self.policy(guild.id)
        support = member.guild_permissions.manage_guild or policy["support_role_id"] in {role.id for role in member.roles}
        if not support and (staff_only or member.id != row["user_id"]):
            raise ValueError("Only this ticket's author and the support team can access it.")
        return member

    async def claim_ticket(self, guild, user_id, item_id, version=None):
        async with self.lock(guild.id):
            row = await self.store.hub_item(guild.id,item_id)
            actor = await self.ticket_access(guild,user_id,row,staff_only=True)
            if row["status"] != "open":
                raise ValueError("Only open tickets can be claimed.")
            if row["data"].get("assigned_id") not in (None,actor.id):
                raise ValueError("This ticket is already claimed by another support member.")
            await self.store.update_hub_item(guild.id,item_id,version=version if version is not None else row["version"],data={"assigned_id":actor.id})

    async def close_ticket(self, guild, user_id, item_id, reason, version=None):
        reason = text(reason,"Close reason",1,500)
        async with self.lock(guild.id):
            row = await self.store.hub_item(guild.id,item_id)
            actor = await self.ticket_access(guild,user_id,row)
            if row["status"] == "closed":
                return
            if row["status"] not in ("open","closing"):
                raise ValueError("Check this ticket's room first. Uncertain requests can be reconciled by refreshing the hub.")
            await self.store.update_hub_item(guild.id,item_id,version=version if version is not None else row["version"],status="closing",data={"close_reason":reason,"closed_by":actor.id})
            try:
                channel = await guild.fetch_channel(row["channel_id"])
            except discord.NotFound:
                await self.store.update_hub_item(guild.id,item_id,status="closed",data={"outcome":"Room was already deleted. Earlier messages cannot be recovered."})
                return
            if not isinstance(channel,discord.TextChannel) or channel.topic != f"espresso-ticket:{item_id}":
                raise ValueError("The ticket room marker changed. Staff must review the room before deleting it.")
            policy = await self.policy(guild.id)
            if row["data"].get("transcripts") and policy["ticket_transcripts"] and not row["data"].get("transcript"):
                # Do not delete the room if reading its opted-in transcript fails.
                messages = [message async for message in channel.history(limit=1001,oldest_first=False)]
                truncated = len(messages)>1000
                lines = [f"Espresso ticket #{item_id}",f"Closed by {actor.id}: {reason}","Recent 1,000 messages only." if truncated else "Text transcript; attachment links may expire.",""]
                size = 0
                for message in reversed(messages[:1000]):
                    line = f"{message.created_at.isoformat()} | {message.author.id} | {message.content}\n" + "\n".join(a.url for a in message.attachments)
                    encoded = line.encode("utf-8")
                    if size+len(encoded)>450_000:
                        lines.append("[Transcript size limit reached.]"); break
                    lines.append(line); size += len(encoded)
                await self.store.update_hub_item(guild.id,item_id,data={"transcript":"\n".join(lines),"transcript_until":time.time()+30*86400})
            try:
                await channel.delete(reason=f"Espresso ticket #{item_id} closed by {actor.id}")
            except discord.NotFound:
                pass
            await self.store.update_hub_item(guild.id,item_id,status="closed",data={"outcome":"Room closed.","closed_at":time.time()})

    async def ticket_transcript(self, guild, user_id, item_id):
        row = await self.store.hub_item(guild.id,item_id)
        await self.ticket_access(guild,user_id,row)
        if not row["data"].get("transcript") or row["data"].get("transcript_until",0)<=time.time():
            raise ValueError("No retained transcript is available for this ticket.")
        return discord.File(io.BytesIO(row["data"]["transcript"].encode("utf-8")),filename=f"ticket-{item_id}.txt")

    async def reconcile_tickets(self, guild, now):
        rows = await self.store.hub_items(guild.id,"ticket",active=True,limit=100)
        uncertain = [row for row in rows if row["status"] in ("posting","uncertain")]
        if not uncertain:
            return
        channels = await guild.fetch_channels()
        for row in uncertain:
            matches = [c for c in channels if isinstance(c,discord.TextChannel) and c.topic==f"espresso-ticket:{row['id']}"]
            if len(matches)==1:
                await self.store.update_hub_item(guild.id,row["id"],status="open",channel_id=matches[0].id,data={"outcome":"Existing room recovered after interrupted creation."})

    async def retire_request(self, guild, user_id, item_id, version):
        await self.manager(guild,user_id)
        async with self.lock(guild.id):
            row = await self.store.hub_item(guild.id,item_id)
            if not row or row["status"] not in ("uncertain","failed"):
                raise ValueError("Only failed or uncertain requests can be retired. Check Discord for a room or message first.")
            await self.store.update_hub_item(guild.id,item_id,version=version,status="removed",data={"outcome":"Retired by staff after checking Discord."})
