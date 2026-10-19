"""Member and staff slash commands for all eight community hub features."""

from datetime import datetime
import time
from typing import Literal

import discord
from discord import app_commands

from .hub import NO_MENTIONS, clean
from .hub_config import text
from .scheduling import local_timestamp


def event_timestamp(value, timezone_name):
    try:
        parsed = datetime.fromisoformat(value.replace("Z","+00:00"))
    except (TypeError,ValueError):
        raise ValueError("Use an ISO date/time, such as 2026-12-01T18:00, and a named timezone.") from None
    return parsed.timestamp() if parsed.tzinfo is not None else local_timestamp(value,timezone_name)


def register_hub_commands(bot):
    from .bot import StaffGroup
    hub = bot.hub_manager
    ticket = app_commands.Group(name="ticket",description="Private support rooms and retained transcripts",guild_only=True)
    events = app_commands.Group(name="events",description="Community events and opt-in reminders",guild_only=True)
    suggestions = StaffGroup("manage_guild",name="suggestions",description="Review community suggestions")
    room = app_commands.Group(name="room",description="Control your temporary café voice room",guild_only=True)
    birthday = app_commands.Group(name="birthday",description="Opt into or remove birthday celebrations",guild_only=True)
    highlights = app_commands.Group(name="highlights",description="Control message highlights and your privacy",guild_only=True)
    profile = app_commands.Group(name="profile",description="Create and customize your optional café profile",guild_only=True)

    @ticket.command(name="open",description="Open a private room with the support team")
    @app_commands.describe(save_transcript="Request a text transcript retained for 30 days, if the server allows it")
    async def open_ticket(interaction: discord.Interaction,category: app_commands.Range[str,1,40],subject: app_commands.Range[str,1,200],save_transcript: bool=False):
        await interaction.response.defer(ephemeral=True)
        row = await hub.open_ticket(interaction.guild,interaction.user.id,category,subject,f"interaction:{interaction.id}",save_transcript=save_transcript)
        message = f"Ticket #{row['id']} · {row['status']}."
        if row["channel_id"]:
            message += f" Open <#{row['channel_id']}>."
        await interaction.followup.send(message,ephemeral=True,allowed_mentions=NO_MENTIONS)

    @open_ticket.autocomplete("category")
    async def ticket_categories(interaction: discord.Interaction,current: str):
        policy = await hub.policy(interaction.guild_id)
        return [app_commands.Choice(name=name,value=name) for name in policy["ticket_categories"] if current.casefold() in name.casefold()][:25]

    @ticket.command(name="mine",description="List your support tickets privately")
    async def my_tickets(interaction: discord.Interaction,before: int | None = None):
        rows = await hub.store.hub_items(interaction.guild_id,"ticket",user_id=interaction.user.id,before=before,limit=21)
        lines = [f"#{row['id']} · {row['status']}" + (f" · <#{row['channel_id']}>" if row["status"]=="open" else "") for row in rows[:20]]
        if len(rows)>20:
            lines.append(f"More: /ticket mine before:{rows[19]['id']}")
        await interaction.response.send_message("\n".join(lines) or "You have no support tickets here.",ephemeral=True,allowed_mentions=NO_MENTIONS)

    @ticket.command(name="claim",description="Support team: claim an open ticket")
    async def claim_ticket(interaction: discord.Interaction,ticket_id: int):
        await interaction.response.defer(ephemeral=True)
        await hub.claim_ticket(interaction.guild,interaction.user.id,ticket_id)
        await interaction.followup.send(f"Ticket #{ticket_id} is assigned to you.",ephemeral=True)

    @ticket.command(name="close",description="Close your ticket or a ticket handled by your support team")
    async def close_ticket(interaction: discord.Interaction,ticket_id: int,reason: app_commands.Range[str,1,500]):
        await interaction.response.defer(ephemeral=True)
        await hub.close_ticket(interaction.guild,interaction.user.id,ticket_id,reason)
        await interaction.followup.send(f"Ticket #{ticket_id} closed. If transcript storage was enabled, use /ticket transcript within 30 days.",ephemeral=True)

    @ticket.command(name="transcript",description="Privately download your ticket's opted-in text transcript")
    async def transcript(interaction: discord.Interaction,ticket_id: int):
        await interaction.response.defer(ephemeral=True)
        file = await hub.ticket_transcript(interaction.guild,interaction.user.id,ticket_id)
        await interaction.followup.send(file=file,ephemeral=True)

    @events.command(name="list",description="Show Espresso's upcoming community events")
    async def list_events(interaction: discord.Interaction):
        await hub.policy(interaction.guild_id,"events")
        rows = await hub.store.hub_items(interaction.guild_id,"event",active=True,limit=20)
        lines = [f"**#{row['id']} · {clean(row['data']['name'],100)}** · <t:{int(row['data']['start_at'])}:f>\nhttps://discord.com/events/{interaction.guild_id}/{row['data']['event_id']}" for row in rows if row["status"] in ("scheduled","active")]
        await interaction.response.send_message("\n\n".join(lines)[:1950] or "No upcoming Espresso events. Managers can create one with /events create.",ephemeral=True)

    @events.command(name="create",description="Managers: create a native Discord event with an external location")
    async def create_event(interaction: discord.Interaction,name: app_commands.Range[str,1,100],location: app_commands.Range[str,1,100],starts: str,ends: str,timezone_name: str="UTC",description: app_commands.Range[str,0,1000]=""):
        await interaction.response.defer(ephemeral=True)
        row = await hub.create_event(interaction.guild,interaction.user.id,name,description,location,event_timestamp(starts,timezone_name),event_timestamp(ends,timezone_name),f"interaction:{interaction.id}")
        await interaction.followup.send(f"Event #{row['id']} · {row['status']}. Members can use /events reminders with this event number.",ephemeral=True)

    @events.command(name="reminders",description="Opt into a 15-minute DM reminder and the event's temporary role")
    async def reminders(interaction: discord.Interaction,event_id: int,enabled: bool=True):
        await interaction.response.defer(ephemeral=True)
        await hub.event_rsvp(interaction.guild,interaction.user.id,event_id,enabled)
        await interaction.followup.send("You opted in. Espresso will DM you during the 15 minutes before the event if your DMs are open. Any configured event role lasts for the event." if enabled else "You unsubscribed. Espresso will remove any event role it granted.",ephemeral=True)

    @events.command(name="end",description="Managers: cancel an upcoming event or end an active one")
    async def end_event(interaction: discord.Interaction,event_id: int):
        await interaction.response.defer(ephemeral=True)
        await hub.end_event(interaction.guild,interaction.user.id,event_id)
        await interaction.followup.send("Event ended and temporary role cleanup queued.",ephemeral=True)

    @events.command(name="badge",description="Managers: award or remove an event badge on an opted-in profile")
    async def event_badge(interaction: discord.Interaction,member: discord.Member,name: app_commands.Range[str,1,40],remove: bool=False):
        await interaction.response.defer(ephemeral=True)
        await hub.manager(interaction.guild,interaction.user.id)
        await hub.policy(interaction.guild_id,"profiles")
        await hub.member(interaction.guild,member.id)
        await hub.store.badge(interaction.guild_id,member.id,name,interaction.user.id,remove)
        await interaction.followup.send("Badge removed." if remove else "Badge awarded.",ephemeral=True)

    @bot.tree.command(name="suggest",description="Submit an idea to this server's suggestion board")
    @app_commands.guild_only()
    async def suggest(interaction: discord.Interaction,idea: app_commands.Range[str,1,1500]):
        await interaction.response.defer(ephemeral=True)
        row = await hub.create_suggestion(interaction.guild,interaction.user.id,idea,f"interaction:{interaction.id}")
        await interaction.followup.send(f"Suggestion #{row['id']} · {row['status']}. Use the buttons on the board to vote.",ephemeral=True)

    @suggestions.command(name="status",description="Publish a staff update and change a suggestion's status")
    async def suggestion_status(interaction: discord.Interaction,suggestion_id: int,status: Literal["open","under_review","accepted","planned","completed","declined"],note: app_commands.Range[str,1,800]):
        await interaction.response.defer(ephemeral=True)
        await hub.suggestion_status(interaction.guild,interaction.user.id,suggestion_id,status,note)
        await interaction.followup.send("Suggestion updated. The staff note is public on the board.",ephemeral=True)

    @room.command(name="rename",description="Rename the temporary voice room you own")
    async def rename_room(interaction: discord.Interaction,name: app_commands.Range[str,1,60]):
        await interaction.response.defer(ephemeral=True)
        await hub.edit_room(interaction.guild,interaction.user.id,name=name)
        await interaction.followup.send("Room renamed.",ephemeral=True)

    @room.command(name="limit",description="Set the user limit on your temporary voice room")
    async def limit_room(interaction: discord.Interaction,count: app_commands.Range[int,1,99]):
        await interaction.response.defer(ephemeral=True)
        await hub.edit_room(interaction.guild,interaction.user.id,user_limit=count)
        await interaction.followup.send("Room limit updated.",ephemeral=True)

    @room.command(name="privacy",description="Lock @everyone out of connecting to your temporary room")
    async def room_privacy(interaction: discord.Interaction,locked: bool):
        await interaction.response.defer(ephemeral=True)
        await hub.edit_room(interaction.guild,interaction.user.id,locked=locked)
        await interaction.followup.send("@everyone cannot connect. Staff and roles with explicit Connect access may still join." if locked else "The original @everyone connection setting is restored.",ephemeral=True)

    @birthday.command(name="set",description="Opt into public birthday greetings; store only month and day")
    async def set_birthday(interaction: discord.Interaction,month: app_commands.Range[int,1,12],day: app_commands.Range[int,1,31]):
        await interaction.response.defer(ephemeral=True)
        await hub.policy(interaction.guild_id,"birthdays")
        await hub.member(interaction.guild,interaction.user.id)
        await hub.store.birthday(interaction.guild_id,interaction.user.id,month,day)
        await interaction.followup.send(f"Birthday saved: {month:02d}-{day:02d}. This opts you into a public greeting and any configured birthday role. February 29 is celebrated February 28 in other years. Use /birthday remove to opt out.",ephemeral=True)

    @birthday.command(name="remove",description="Delete your stored birthday and opt out of greetings")
    async def remove_birthday(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with hub.lock(interaction.guild_id):
            await hub.store.birthday(interaction.guild_id,interaction.user.id)
            for row in await hub.store.role_grants(interaction.guild_id):
                if row["user_id"]==interaction.user.id and row["purpose"].startswith("birthday:"):
                    await hub.store.write_role_grant(interaction.guild_id,row["purpose"],row["user_id"],row["role_id"],0,row["status"])
            await hub.cleanup_roles(interaction.guild,time.time())
        await interaction.followup.send("Your birthday was deleted. Any birthday role Espresso granted is queued for removal.",ephemeral=True)

    @highlights.command(name="optout",description="Stop your messages from appearing on this server's highlight board")
    async def highlight_optout(interaction: discord.Interaction,enabled: bool=True):
        await interaction.response.defer(ephemeral=True)
        await hub.set_highlight_optout(interaction.guild,interaction.user.id,enabled)
        await interaction.followup.send("You opted out. Existing highlights of your messages are being removed." if enabled else "Future messages can appear on the board again. Removed highlights stay removed.",ephemeral=True)

    @highlights.command(name="remove",description="Managers: remove a highlight and block automatic reposting")
    async def remove_highlight(interaction: discord.Interaction,highlight_id: int):
        await interaction.response.defer(ephemeral=True)
        await hub.remove_highlight(interaction.guild,interaction.user.id,highlight_id)
        await interaction.followup.send("Highlight blocked and removal requested. The hub shows any delivery issue.",ephemeral=True)

    @profile.command(name="edit",description="Opt into a café profile and customize its bio and color")
    async def edit_profile(interaction: discord.Interaction,bio: app_commands.Range[str,0,160]="",theme: Literal["rose","lavender","mocha"]="rose"):
        await interaction.response.defer(ephemeral=True)
        await hub.policy(interaction.guild_id,"profiles")
        await hub.member(interaction.guild,interaction.user.id)
        await hub.store.save_profile(interaction.guild_id,interaction.user.id,bio,theme)
        await interaction.followup.send("Your café profile is ready. Members can view it with /profile show and send limited /thanks awards. Use /profile delete to remove it.",ephemeral=True)

    @profile.command(name="show",description="View an opted-in member's café card")
    async def show_profile(interaction: discord.Interaction,member: discord.Member | None=None):
        await interaction.response.defer()
        member = await hub.member(interaction.guild,(member or interaction.user).id)
        await interaction.followup.send(embed=await hub.profile_embed(interaction.guild,member),allowed_mentions=NO_MENTIONS)

    @profile.command(name="delete",description="Delete your café profile, badge collection, and public thanks history")
    async def delete_profile(interaction: discord.Interaction):
        await hub.store.delete_profile(interaction.guild_id,interaction.user.id)
        await interaction.response.send_message("Your café profile and badges were deleted. A short-lived award limit remains for 24 hours to prevent resets.",ephemeral=True)

    @bot.tree.command(name="thanks",description="Give a helpful member a limited community thank-you")
    @app_commands.guild_only()
    async def thanks(interaction: discord.Interaction,member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        await hub.policy(interaction.guild_id,"profiles")
        await hub.member(interaction.guild,interaction.user.id)
        await hub.member(interaction.guild,member.id)
        created = await hub.store.give_thanks(interaction.guild_id,interaction.user.id,member.id,f"interaction:{interaction.id}")
        await interaction.followup.send("Thank-you awarded. Their café card has been updated." if created else "This thank-you was already recorded.",ephemeral=True)

    async def saved_reply(interaction: discord.Interaction,name: str):
        await interaction.response.defer()
        await hub.policy(interaction.guild_id,"faq")
        await hub.member(interaction.guild,interaction.user.id)
        entry = next((row for row in await hub.store.faq_entries(interaction.guild_id) if row["name"]==name),None)
        if entry is None:
            raise ValueError("Choose a saved reply from autocomplete. Managers edit these in Community hub.")
        await interaction.followup.send(entry["answer"],allowed_mentions=NO_MENTIONS,suppress_embeds=True)

    async def faq_choices(interaction: discord.Interaction,current: str):
        policy = await hub.policy(interaction.guild_id)
        if not policy["faq_enabled"]:
            return []
        return [app_commands.Choice(name=(row["name"]+" · "+row["summary"])[:100],value=row["name"]) for row in await hub.store.faq_entries(interaction.guild_id) if current.casefold() in (row["name"]+" "+row["summary"]).casefold()][:25]

    for name in ("faq","tag"):
        command = app_commands.Command(name=name,description="Look up a staff-written saved reply",callback=saved_reply)
        command.guild_only = True
        command.autocomplete("name")(faq_choices)
        bot.tree.add_command(command)
    for group in (ticket,events,suggestions,room,birthday,highlights,profile):
        bot.tree.add_command(group)
