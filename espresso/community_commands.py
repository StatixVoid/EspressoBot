"""Discord entry points for the staff desk and opt-in café features."""

from datetime import datetime
import re
from typing import Literal

import discord
from discord import app_commands

from .coffee_menu import DRINKS, CHARACTERS, drink_choice, character_choice


class CoffeeMenuView(discord.ui.View):
    """A short-lived private order; posting always rechecks the live server policy."""

    def __init__(self, features, owner_id, guild_id, member, character):
        super().__init__(timeout=180)
        self.features, self.owner_id, self.guild_id, self.member = features, owner_id, guild_id, member
        self.drink, self.character = "surprise", character
        self.sending = False
        self.sent = False
        self.drink_select = discord.ui.Select(placeholder="Choose your drink",row=0,options=[
            discord.SelectOption(label="Surprise me",value="surprise",description="Let the café choose your next sip",emoji="✨",default=True),
            *(discord.SelectOption(label=drink["name"],value=drink["id"],description=drink["note"][:100],emoji=drink["emoji"]) for drink in DRINKS),
        ])
        self.character_select = discord.ui.Select(placeholder="Choose a café hero",row=1,options=[
            discord.SelectOption(label=item["name"],value=item["id"],description=item["tagline"][:100],default=item["id"] == character) for item in CHARACTERS
        ])
        self.send_button = discord.ui.Button(label="Send card",style=discord.ButtonStyle.primary,emoji="☕",row=2)
        self.drink_select.callback = self.choose_drink
        self.character_select.callback = self.choose_character
        self.send_button.callback = self.send_card
        for item in (self.drink_select,self.character_select,self.send_button):
            self.add_item(item)

    def embed(self):
        selected = drink_choice(self.drink)
        hero = character_choice(self.character)
        embed = discord.Embed(title="Espresso's beverage menu",description="Pick a drink and a café companion, then send a little kindness.\nYour card will appear in this channel, without a notification ping.",color=0xC9B5EB)
        for category,label in (("coffee","☕ Coffee"),("cocoa","🍫 Cocoa"),("tea","🍵 Tea")):
            embed.add_field(name=label,value=" · ".join(item["name"] for item in DRINKS if item["category"] == category),inline=False)
        embed.add_field(name="Your order",value=(f"**{selected['name']}** — {selected['note']}" if selected else "**Surprise me** — a random drink from the whole menu.") + f"\nCompanion: **{hero['name']}**",inline=False)
        embed.set_footer(text="This menu closes after 3 minutes · coffee cards share a 30-second cooldown")
        return embed

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Open your own menu with /drinks to make an order.",ephemeral=True)
            return False
        return True

    async def choose_drink(self, interaction):
        self.drink = self.drink_select.values[0]
        drink_choice(self.drink)
        for option in self.drink_select.options:
            option.default = option.value == self.drink
        await interaction.response.edit_message(embed=self.embed(),view=self)

    async def choose_character(self, interaction):
        self.character = self.character_select.values[0]
        character_choice(self.character)
        for option in self.character_select.options:
            option.default = option.value == self.character
        await interaction.response.edit_message(embed=self.embed(),view=self)

    async def send_card(self, interaction):
        if self.sent or self.sending:
            await interaction.response.send_message("This order is already being served. Open /drinks for another.",ephemeral=True)
            return
        self.sending = True
        try:
            await self.features.coffee(interaction,self.member,self.drink,self.character)
        except (ValueError,discord.HTTPException,OSError) as exc:
            message = str(exc) if isinstance(exc,ValueError) else "Your café card could not be confirmed. Check the channel before trying again."
            if interaction.response.is_done():
                await interaction.followup.send(message,ephemeral=True)
            else:
                await interaction.response.send_message(message,ephemeral=True)
            return
        finally:
            self.sending = False
        self.sent = True
        for item in self.children:
            item.disabled = True
        self.stop()
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass


def register_community_commands(bot):
    from .bot import StaffGroup, safe
    features = bot.community_manager

    async def submit_report(interaction, reason, source, member=None, message_url=""):
        if not interaction.guild or not isinstance(interaction.user,discord.Member):
            raise ValueError("Use reports in a server.")
        if message_url:
            match = re.fullmatch(r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)",message_url)
            if not match or int(match[1]) != interaction.guild_id:
                raise ValueError("Use a Discord message link from this server.")
            channel = interaction.guild.get_channel_or_thread(int(match[2]))
            if channel is None:
                channel = await interaction.guild.fetch_channel(int(match[2]))
            if channel.guild.id != interaction.guild_id or not channel.permissions_for(interaction.user).view_channel or not channel.permissions_for(interaction.user).read_message_history:
                raise ValueError("You must be able to view the reported message.")
            if isinstance(channel,discord.Thread) and channel.is_private() and not interaction.user.guild_permissions.manage_threads:
                try:
                    await channel.fetch_member(interaction.user.id)
                except discord.NotFound:
                    raise ValueError("Join that private thread before reporting one of its messages.") from None
            message = await channel.fetch_message(int(match[3]))
            member = message.author
            message_url = f"https://discord.com/channels/{interaction.guild_id}/{channel.id}/{message.id}"
        if member is None:
            raise ValueError("Choose a member or a message to report.")
        item_id = await features.store.create_inbox(interaction.guild_id,"report",interaction.user.id,reason,source,
                                                   target_id=member.id,message_url=message_url)
        await bot.log_action(interaction.guild,"Private report received",f"Staff inbox item #{item_id}. Details are available only to staff with Moderate Members.")
        return item_id

    @bot.tree.command(name="report",description="Privately report a member or message to this server's moderators")
    @app_commands.guild_only()
    async def report(interaction: discord.Interaction, member: discord.Member,
                     reason: app_commands.Range[str,1,1500], message_url: str | None = None):
        await interaction.response.defer(ephemeral=True)
        item_id = await submit_report(interaction,reason,f"interaction:{interaction.id}",member,message_url or "")
        await interaction.followup.send(f"Report #{item_id} sent privately to this server's moderators.",ephemeral=True)

    class ReportModal(discord.ui.Modal,title="Report to Espresso"):
        reason = discord.ui.TextInput(label="What should moderators know?",style=discord.TextStyle.paragraph,min_length=1,max_length=1500)

        def __init__(self, message):
            super().__init__()
            self.message_url = message.jump_url

        async def on_submit(self, interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                item_id = await submit_report(interaction,str(self.reason),f"interaction:{interaction.id}",message_url=self.message_url)
                await interaction.followup.send(f"Report #{item_id} sent privately to staff.",ephemeral=True)
            except (ValueError,discord.HTTPException) as exc:
                await interaction.followup.send(str(exc) if isinstance(exc,ValueError) else "Could not access that message. Check channel permissions and retry.",ephemeral=True)

        async def on_error(self, interaction, error):
            if interaction.response.is_done():
                await interaction.followup.send("The report could not be confirmed. Try again shortly.",ephemeral=True)
            else:
                await interaction.response.send_message("The report could not be confirmed. Try again shortly.",ephemeral=True)

    @bot.tree.context_menu(name="Report to Espresso")
    @app_commands.guild_only()
    async def report_message(interaction: discord.Interaction, message: discord.Message):
        await interaction.response.send_modal(ReportModal(message))

    @bot.tree.command(name="appeal",description="Privately appeal one of your moderation cases")
    @app_commands.guild_only()
    async def appeal(interaction: discord.Interaction, case_number: app_commands.Range[int,1],
                     reason: app_commands.Range[str,1,1500]):
        await interaction.response.defer(ephemeral=True)
        item_id = await features.store.create_inbox(interaction.guild_id,"appeal",interaction.user.id,reason,
                                                    f"interaction:{interaction.id}",case_id=case_number)
        await bot.log_action(interaction.guild,"Private appeal received",f"Staff inbox item #{item_id}; case #{case_number}. Details are available to authorized staff.")
        await interaction.followup.send(f"Appeal #{item_id} submitted. Staff will review it; existing actions remain until staff change them.",ephemeral=True)

    @bot.tree.command(name="mycases",description="Privately view your recent moderation cases and case numbers")
    @app_commands.guild_only()
    async def mycases(interaction: discord.Interaction):
        rows = await features.store.cases(interaction.guild_id,user_id=interaction.user.id)
        lines = [f"**Case #{row['id']}** · {row['kind'].replace('_',' ')}" + (" · warning removed" if row["voided_at"] else "") + f"\n{safe(row['reason'],150)}" for row in rows[:10]]
        await interaction.response.send_message("\n\n".join(lines) or "You have no recorded cases in this server.",ephemeral=True)

    @bot.tree.command(name="myreports",description="Privately check your reports, appeals, and member-facing outcomes")
    @app_commands.guild_only()
    async def myreports(interaction: discord.Interaction, before: app_commands.Range[int,1] | None = None):
        rows = await features.store.member_reports(interaction.guild_id,interaction.user.id,before)
        text = "\n\n".join(f"**#{row['id']}** · {row['kind']} · {row['status'].replace('_',' ')}\n{safe(row['public_response'],250) if row['public_response'] else 'No member-facing outcome posted yet.'}" for row in rows[:10])
        if len(rows) > 10:
            text += f"\n\nOlder items: /myreports before:{rows[9]['id']}"
        await interaction.response.send_message(text or "You have no reports or appeals in this server.",ephemeral=True)

    @bot.tree.command(name="coffee",description="Send someone a little Espresso coffee card")
    @app_commands.guild_only()
    @app_commands.describe(drink="Pick a beverage, or leave surprise for a random order",character="Choose a café companion, or use this server's default")
    @app_commands.choices(character=[app_commands.Choice(name=item["name"],value=item["id"]) for item in CHARACTERS])
    async def coffee(interaction: discord.Interaction, member: discord.Member, drink: str = "surprise",
                     character: Literal["sabrina","guardian","dreamer","spark","none"] | None = None):
        await features.coffee(interaction,member,drink,character)

    @coffee.autocomplete("drink")
    async def coffee_drink_choices(interaction: discord.Interaction, current: str):
        query = current.casefold().strip()
        choices = [app_commands.Choice(name="Surprise me",value="surprise")] if not query or query in "surprise me" else []
        choices.extend(app_commands.Choice(name=item["name"],value=item["id"]) for item in DRINKS
                       if not query or query in item["name"].casefold() or query in item["id"] or query in item["category"])
        return choices[:25]

    @bot.tree.command(name="drinks",description="Privately browse the beverage menu and build a café card")
    @app_commands.guild_only()
    @app_commands.describe(member="Who is the card for? Leave empty for a treat for yourself")
    async def drinks(interaction: discord.Interaction, member: discord.Member | None = None):
        features.require_public_post(interaction)
        settings = await features.store.settings(interaction.guild_id)
        if not settings.community["coffee_enabled"]:
            raise ValueError("Coffee cards are off. Ask an admin to enable them in Community settings.")
        view = CoffeeMenuView(features,interaction.user.id,interaction.guild_id,member or interaction.user,settings.community["coffee_character"])
        await interaction.response.send_message(embed=view.embed(),view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @bot.tree.command(name="poll",description="Create a community poll; separate answer options with |")
    @app_commands.guild_only()
    async def poll(interaction: discord.Interaction, question: app_commands.Range[str,1,300],
                   options: app_commands.Range[str,3,550], hours: app_commands.Range[int,1,168] = 24):
        await features.poll(interaction,question,options,hours)

    desk = StaffGroup("moderate_members",name="staffdesk",description="Private reports, appeals, and moderation cases")

    @desk.command(name="inbox",description="Show recent private reports and appeals awaiting review")
    async def inbox(interaction: discord.Interaction):
        rows = await features.store.inbox(interaction.guild_id)
        text = "\n\n".join(f"**#{r['id']}** · {r['kind']} · {r['status']} · version {r['version']}\n{safe(r['body'],200)}" for r in rows[:10])
        await interaction.response.send_message(text or "No reports or appeals are waiting.",ephemeral=True)

    @desk.command(name="review",description="Claim, resolve, reject, or reopen a report or appeal")
    async def review(interaction: discord.Interaction, item: app_commands.Range[int,1], version: app_commands.Range[int,1],
                     action: Literal["claim","resolve","reject","reopen"], note: app_commands.Range[str,1,1000] = "",
                     member_response: app_commands.Range[str,1,1000] = "", notify: bool = False):
        await interaction.response.defer(ephemeral=True)
        status = await features.review(interaction.guild,interaction.user,item,version,action,note,member_response,notify)
        await interaction.followup.send(f"Item #{item}: {status}. Resolving an appeal records the decision; remove a mistaken warning separately.",ephemeral=True)

    @desk.command(name="cases",description="View a member's recent moderation case numbers")
    async def cases(interaction: discord.Interaction, member: discord.Member):
        rows = await features.store.cases(interaction.guild_id,user_id=member.id)
        text = "\n\n".join(f"**Case #{r['id']}** · {r['kind']} · <t:{int(r['created_at'])}:f>\n{safe(r['reason'],150)}\n{safe(r['outcome'],150)}" for r in rows[:10])
        await interaction.response.send_message(text or "No cases recorded.",ephemeral=True)

    @desk.command(name="removewarning",description="Remove one mistaken warning while keeping an audit record")
    async def removewarning(interaction: discord.Interaction, warning: app_commands.Range[int,1], reason: app_commands.Range[str,1,1000]):
        await interaction.response.defer(ephemeral=True)
        changed = await features.remove_warning(interaction.guild,interaction.user,warning,reason)
        await interaction.followup.send("Warning removed from active history. Existing timeout is unchanged." if changed else "That warning was already removed.",ephemeral=True)

    bot.tree.add_command(desk)
    community = StaffGroup("manage_guild",name="community",description="Community tools, schedules, and server checks")

    @community.command(name="schedule",description="Schedule a post; use an ISO date with timezone, such as 2026-12-01T18:00-07:00")
    async def schedule(interaction: discord.Interaction, channel: discord.TextChannel,
                       when: str, message: app_commands.Range[str,1,1800],
                       repeat_days: app_commands.Range[int,0,365] = 0, notify: bool = False,
                       timezone_name: str | None = None, recurrence: Literal["once","interval","daily","weekly"] = "interval",
                       weekdays: str = ""):
        from .scheduling import calendar_policy, local_timestamp, next_occurrence, zone
        try:
            date = datetime.fromisoformat(when.replace("Z","+00:00"))
        except ValueError:
            raise ValueError("Use a date and time with timezone, such as 2026-12-01T18:00-07:00.") from None
        if date.tzinfo is None and timezone_name is None:
            raise ValueError("Include a timezone offset, or choose a named timezone such as America/Phoenix.")
        if recurrence in ("daily","weekly") and timezone_name is None:
            raise ValueError("Choose a named timezone for local-clock daily or weekly repeats.")
        tz = zone(timezone_name or "UTC")
        due = local_timestamp(when,timezone_name) if date.tzinfo is None else date.timestamp()
        policy = calendar_policy({"timezone":timezone_name or "UTC","recurrence":recurrence,
                                  "weekdays":[int(day.strip()) for day in weekdays.split(",") if day.strip()],
                                  "local_time":datetime.fromtimestamp(due,tz).strftime("%H:%M")})
        if recurrence in ("daily","weekly"):
            due = next_occurrence(due-1,policy)
        await interaction.response.defer(ephemeral=True)
        post_id = await features.schedule(interaction.guild,interaction.user,channel.id,message,due,repeat_days,notify,f"interaction:{interaction.id}",policy)
        await interaction.followup.send(f"Post #{post_id} scheduled for <t:{int(due)}:F> · {policy['timezone']} · {recurrence}.",ephemeral=True)

    @community.command(name="posts",description="Privately list upcoming and recent scheduled posts")
    async def posts(interaction: discord.Interaction):
        rows = await features.store.posts(interaction.guild_id)
        await interaction.response.send_message("\n".join(f"#{row['id']} · {row['status']} · <t:{int(row['due_at'])}:F> · channel {row['channel_id']}" for row in rows[:20]) or "No scheduled posts.",ephemeral=True)

    @community.command(name="cancelpost",description="Cancel a pending scheduled post")
    async def cancelpost(interaction: discord.Interaction, post: app_commands.Range[int,1]):
        await features.store.cancel_post(interaction.guild_id,post)
        await bot.log_action(interaction.guild,"Scheduled post cancelled",f"Post #{post} · moderator {interaction.user.id}.")
        await interaction.response.send_message(f"Post #{post} cancelled.",ephemeral=True)

    @community.command(name="testfilter",description="Test text rules privately without deleting messages or recording warnings")
    async def testfilter(interaction: discord.Interaction, text: app_commands.Range[str,1,4000], channel: discord.TextChannel | None = None,
                         member: discord.Member | None = None, active_warnings: app_commands.Range[int,0,100] = 0,
                         recent_messages: app_commands.Range[int,0,50] = 0, warning_cooldown: bool = False, edited: bool = False):
        from .simulation import simulate
        from .bot import target_error
        await interaction.response.defer(ephemeral=True)
        settings = await features.store.settings(interaction.guild_id)
        context = {"active_warnings":active_warnings,"recent_messages":recent_messages,"warning_cooldown":warning_cooldown,"edited":edited}
        timeout_error = None
        if member is not None:
            member = await interaction.guild.fetch_member(member.id)
            context.update(role_ids=[role.id for role in member.roles],administrator=member.guild_permissions.administrator,
                           owner=member.id == interaction.guild.owner_id,bot=member.bot)
            timeout_error = target_error(None,member,interaction.guild.me)
            if timeout_error is None and not interaction.guild.me.guild_permissions.moderate_members:
                timeout_error = "Espresso needs Moderate Members."
        selected_channel = channel or interaction.channel
        result = simulate(text,settings,context,channel_id=selected_channel.id,parent_id=getattr(selected_channel,"parent_id",None),timeout_error=timeout_error)
        await interaction.followup.send((f"Matched **{result['rule']}**; configured action: **{result['action']}**." if result["matched"] else "No text rule matched.") + f"\nModeration: {'on' if result['moderation_enabled'] else 'off'}; observation: {'on' if result['observation_mode'] else 'off'}.\n"+result["note"],ephemeral=True)

    @community.command(name="health",description="Check permissions, role placement, and channel setup")
    async def health(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        checks = await features.diagnostics(interaction.guild)
        await interaction.followup.send("\n".join(f"{'✅' if item['ok'] else '⚠️'} **{item['name']}** — {item['detail']}" for item in checks)[:3900],ephemeral=True)

    @community.command(name="endprotection",description="End temporary raid restrictions and restore previous channel settings")
    async def endprotection(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await features.end_protection(interaction.guild,interaction.user)
        await interaction.followup.send(f"Restored {result['restored']} channel(s); {result['remaining']} need permission fixes before restoration.",ephemeral=True)

    bot.tree.add_command(community)
