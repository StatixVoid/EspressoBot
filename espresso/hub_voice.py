"""Lobby-created voice rooms, owner controls, and restart-safe empty-room cleanup."""

import discord
import time

from .hub_config import text


class VoiceFeatures:
    async def voice_changed(self, member, before, after):
        if member.bot or before.channel == after.channel:
            return
        guild = member.guild
        policy = await self.policy(guild.id)
        async with self.lock(guild.id):
            await self.cleanup_voice(guild,0)
            if not policy["voice_enabled"] or after.channel is None or after.channel.id != policy["voice_lobby_id"]:
                return
            member = await self.member(guild,member.id)
            lobby = await guild.fetch_channel(policy["voice_lobby_id"])
            parent = await guild.fetch_channel(policy["voice_category_id"])
            if not isinstance(lobby,discord.VoiceChannel) or not isinstance(parent,discord.CategoryChannel):
                raise ValueError("The voice lobby or room category needs to be configured again.")
            if not all(getattr(lobby.permissions_for(member),p) for p in ("view_channel","connect")):
                return
            if guild.me is None or not guild.me.guild_permissions.move_members or not all(getattr(parent.permissions_for(guild.me),p) for p in ("view_channel","connect","manage_channels","manage_roles")):
                raise ValueError("Temporary voice room permissions changed. Ask a manager to check the hub.")
            existing = await self.store.hub_items(guild.id,"voice",active=True,user_id=member.id)
            if existing:
                if existing[0]["status"] == "open" and existing[0]["channel_id"]:
                    room = await guild.fetch_channel(existing[0]["channel_id"])
                    await member.move_to(room,reason="Espresso existing personal room")
                return
            data = {"category_id":parent.id,"lobby_id":lobby.id,"base_connect":lobby.overwrites_for(guild.default_role).connect,"locked":False,"name":"Personal café","user_limit":policy["voice_user_limit"]}
            row,_ = await self.store.begin_hub_item(guild.id,"voice",member.id,data,f"voice:{member.id}:{time.time_ns()}")
            overwrites = dict(lobby.overwrites)
            overwrites[member] = discord.PermissionOverwrite(view_channel=True,connect=True)
            overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True,connect=True,manage_channels=True)
            try:
                room = await guild.create_voice_channel(f"espresso-room-{row['id']}",category=parent,overwrites=overwrites,user_limit=policy["voice_user_limit"],reason="Espresso personal voice room")
            except discord.Forbidden:
                await self.store.update_hub_item(guild.id,row["id"],status="failed",data={"outcome":"Discord refused room creation."})
                raise
            except discord.HTTPException:
                await self.store.update_hub_item(guild.id,row["id"],status="uncertain",data={"outcome":"Check the room category before retiring this request."})
                raise
            await self.store.update_hub_item(guild.id,row["id"],status="open",channel_id=room.id)
            # Moving a member needs a fresh voice state: they may already have left the lobby.
            if member.id in lobby.voice_states:
                await member.move_to(room,reason="Espresso personal voice room")

    async def edit_room(self, guild, user_id, *, name=None, user_limit=None, locked=None):
        await self.policy(guild.id,"voice")
        member = await self.member(guild,user_id)
        async with self.lock(guild.id):
            rows = await self.store.hub_items(guild.id,"voice",active=True,user_id=member.id)
            if not rows or rows[0]["status"] != "open":
                raise ValueError("Join the configured voice lobby to create your room first.")
            row = rows[0]
            room = await guild.fetch_channel(row["channel_id"])
            if not isinstance(room,discord.VoiceChannel):
                raise ValueError("Your temporary voice room no longer exists.")
            changes,stored = {},{}
            if name is not None:
                changes["name"] = text(name,"Room name",1,60)
                if "@" in changes["name"]:
                    raise ValueError("Room names cannot contain mentions.")
                stored["name"] = changes["name"]
            if user_limit is not None:
                if type(user_limit) is not int or not 1 <= user_limit <= 99:
                    raise ValueError("Choose a user limit between 1 and 99.")
                changes["user_limit"] = stored["user_limit"] = user_limit
            if locked is not None:
                if type(locked) is not bool:
                    raise ValueError("Choose whether the room is locked.")
                overwrite = room.overwrites_for(guild.default_role)
                overwrite.connect = False if locked else row["data"].get("base_connect")
                await room.set_permissions(guild.default_role,overwrite=overwrite,reason="Espresso room owner connection setting")
                stored["locked"] = locked
            if changes:
                await room.edit(**changes,reason="Espresso room owner settings")
            await self.store.update_hub_item(guild.id,row["id"],data=stored)

    async def cleanup_voice(self, guild, now):
        rows = await self.store.hub_items(guild.id,"voice",active=True,limit=30)
        channels = None
        for row in rows:
            if row["status"] in ("uncertain","posting"):
                if channels is None:
                    channels = await guild.fetch_channels()
                matches = [c for c in channels if isinstance(c,discord.VoiceChannel) and c.name==f"espresso-room-{row['id']}" and c.category_id==row["data"].get("category_id")]
                if len(matches)==1:
                    await self.store.update_hub_item(guild.id,row["id"],status="open",channel_id=matches[0].id)
                    row["channel_id"] = matches[0].id
                else:
                    continue
            if not row["channel_id"]:
                continue
            # Gateway channel voice_states works with member caching disabled.
            room = guild.get_channel(row["channel_id"])
            if room is None:
                try:
                    room = await guild.fetch_channel(row["channel_id"])
                except discord.NotFound:
                    await self.store.update_hub_item(guild.id,row["id"],status="removed")
                    continue
            if not isinstance(room,discord.VoiceChannel) or room.voice_states:
                continue
            try:
                await room.delete(reason="Espresso empty personal room cleanup")
            except discord.NotFound:
                pass
            except discord.HTTPException:
                continue
            await self.store.update_hub_item(guild.id,row["id"],status="removed")
