"""Account-age screening, explicit rule agreement, and staff-approved access roles."""

import time

import discord

from .reaction_roles import role_error
from .screening_config import needs_review, rules_revision


class ScreeningManager:
    def __init__(self, bot):
        self.bot = bot

    async def policy(self, guild_id):
        return (await self.bot.store.settings(guild_id)).screening

    async def validate(self, guild, actor, config):
        if not config["enabled"]:
            return
        if not self.bot.intents.members:
            raise ValueError("Automatic join tracking is unavailable. Ask Espresso's operator to enable member events before turning screening on.")
        if not actor.guild_permissions.manage_guild or not actor.guild_permissions.manage_roles:
            raise ValueError("You need Manage Server and Manage Roles to configure member screening.")
        await self.access_role(guild, config, actor)
        channel = next((c for c in await guild.fetch_channels() if c.id == config["entry_channel_id"]),None)
        if not isinstance(channel,discord.TextChannel):
            raise ValueError("Choose a text channel in this server for verification.")
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links and channel.permissions_for(actor).view_channel):
            raise ValueError("You must be able to view the entry channel; Espresso needs View Channel, Send Messages, and Embed Links.")

    async def access_role(self, guild, config, actor=None):
        role = next((r for r in await guild.fetch_roles() if r.id == config["verified_role_id"]),None)
        error = role_error(role,guild,guild.me,actor)
        if error:
            raise ValueError(error.replace("Reaction roles","Verified roles").replace("reaction roles","member screening").replace("reaction role","verified role"))
        return role

    async def _case(self, member, config):
        case = await self.bot.store.screening_case(member.guild.id,member.id)
        joined = (member.joined_at or discord.utils.utcnow()).timestamp()
        if case is None or abs(case["joined_at"]-joined) > .001:
            case = {"guild_id":member.guild.id,"user_id":member.id,"joined_at":joined,
                    "account_created_at":member.created_at.timestamp(),"requires_review":needs_review(config,member.created_at.timestamp(),joined),
                    "agreed_at":None,"rules_hash":"","status":"awaiting_agreement" if config["require_agreement"] else "pending",
                    "reviewer_id":None,"role_id":None,"updated_at":time.time(),"version":case["version"] if case else 0}
            case = await self.bot.store.save_screening_case(case,"joined")
            age = max(0,int((joined-case["account_created_at"])/86400))
            await self.bot.log_action(member.guild,"Member screening",f"Member {member.id} · account age at join {age} day(s) · manual review {bool(case['requires_review'])}. Access role withheld.")
        return case

    async def assess(self, guild, user_id, agreement_hash=None):
        async with self.bot.member_lock(guild.id,user_id):
            config = await self.policy(guild.id)
            if not config["enabled"]:
                raise ValueError("Member screening is not enabled for this server.")
            member = await guild.fetch_member(user_id)
            if member.bot or member.id == guild.owner_id or member.guild_permissions.administrator:
                return "This account is already trusted by the server."
            if any(role.id == config["verified_role_id"] for role in member.roles):
                return "You already have this server's verified access role."
            case = await self._case(member,config)
            if case["status"] == "denied":
                return "Your request was declined. Contact server staff if you need another review."
            revision = rules_revision(config)
            if agreement_hash is not None:
                if agreement_hash != revision:
                    raise ValueError("The rules changed. Read them again with /verify before agreeing.")
                if getattr(member,"pending",False):
                    raise ValueError("Complete Discord's built-in server rules screening first, then use /verify again.")
                if case["rules_hash"] != revision or not case["agreed_at"]:
                    case.update(agreed_at=time.time(),rules_hash=revision,status="pending")
                    case = await self.bot.store.save_screening_case(case,"agreed",user_id)
            agreed = not config["require_agreement"] or (case["agreed_at"] and case["rules_hash"] == revision)
            if not agreed:
                return "Read this server's rules with /verify, then choose I agree & request access."
            if getattr(member,"pending",False):
                return "Complete Discord's built-in server rules first, then use /verify again."
            review = bool(case["requires_review"] or needs_review(config,case["account_created_at"],case["joined_at"]))
            if review:
                if case["status"] != "pending" or not case["requires_review"]:
                    case.update(status="pending",requires_review=True)
                    await self.bot.store.save_screening_case(case,"queued")
                return "Your agreement is recorded. Server staff must approve your access."
            role = await self.access_role(guild,config)
            if await self.policy(guild.id) != config:
                raise ValueError("Screening settings changed. Please use /verify again.")
            await member.add_roles(role,reason="Espresso verification: account-age policy and required agreement passed",atomic=True)
            case.update(status="approved",role_id=role.id,reviewer_id=None)
            await self.bot.store.save_screening_case(case,"auto_approved")
            await self.bot.log_action(guild,"Verification approved",f"Member {user_id} · role {role.id} · account-age policy passed.")
            return "You're verified. Your server access role has been granted."

    async def joined(self, member):
        if not (await self.policy(member.guild.id))["enabled"]:
            return
        try:
            await self.assess(member.guild,member.id)
        except (ValueError,discord.HTTPException):
            await self.bot.log_action(member.guild,"Verification needs attention",f"Member {member.id} · access role was not granted. Check screening settings, role hierarchy, and Discord screening.")

    async def left(self, guild_id, user_id):
        async with self.bot.member_lock(guild_id,user_id):
            case = await self.bot.store.screening_case(guild_id,user_id)
            if not case or case["status"] == "departed":
                return
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                try:
                    await guild.fetch_member(user_id)
                    return  # A quick rejoin must not be closed by an older leave event.
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    return
            case["status"] = "departed"
            await self.bot.store.save_screening_case(case,"left")

    async def review(self, guild, actor, user_id, action, version):
        if not (actor.guild_permissions.manage_guild and actor.guild_permissions.manage_roles):
            raise ValueError("You need Manage Server and Manage Roles to review access requests.")
        if actor.id == user_id:
            raise ValueError("You cannot review your own verification request.")
        if action not in ("approve","deny","reopen"):
            raise ValueError("Choose approve, deny, or reopen.")
        async with self.bot.member_lock(guild.id,user_id):
            config = await self.policy(guild.id)
            if not config["enabled"]:
                raise ValueError("Member screening is disabled.")
            case = await self.bot.store.screening_case(guild.id,user_id)
            if not case or case["version"] != version:
                raise ValueError("This request changed. Refresh the review queue.")
            if case["status"] == "approved":
                raise ValueError("This request is already approved. This screen does not revoke roles.")
            member = await guild.fetch_member(user_id)
            joined = (member.joined_at or discord.utils.utcnow()).timestamp()
            if abs(case["joined_at"]-joined) > .001:
                raise ValueError("This member rejoined. Ask them to use /verify to create a current request.")
            role = await self.access_role(guild,config,actor)
            if action == "approve":
                if case["status"] == "denied":
                    raise ValueError("Reopen a declined request before approving it.")
                if config["require_agreement"] and (not case["agreed_at"] or case["rules_hash"] != rules_revision(config)):
                    raise ValueError("The member must agree to the current rules with /verify first.")
                if getattr(member,"pending",False):
                    raise ValueError("The member must complete Discord's built-in rules screening first.")
                if await self.policy(guild.id) != config:
                    raise ValueError("Screening settings changed. Refresh before approving.")
                await member.add_roles(role,reason=f"Espresso verification approved by {actor.id}",atomic=True)
                case.update(status="approved",role_id=role.id)
            elif action == "deny":
                case["status"] = "denied"
            else:
                case["status"] = "pending" if not config["require_agreement"] or (case["agreed_at"] and case["rules_hash"] == rules_revision(config)) else "awaiting_agreement"
            case["reviewer_id"] = actor.id
            case = await self.bot.store.save_screening_case(case,action,actor.id)
            await self.bot.log_action(guild,"Verification review",f"Member {user_id} · reviewer {actor.id} · action {action}. Declining or reopening does not kick, ban, or remove roles.")
            return case

    async def publish(self, guild, actor):
        config = await self.policy(guild.id)
        if not config["enabled"]:
            raise ValueError("Save and enable screening first.")
        await self.validate(guild,actor,config)
        channel = next(c for c in await guild.fetch_channels() if c.id == config["entry_channel_id"])
        embed = discord.Embed(title="☕ Welcome · verify your access",description="Read the server rules and request access below. Staff review may be required. No access role is granted until the configured checks pass.",color=0xD8A7B1)
        message = await channel.send(embed=embed,view=ScreeningEntryView(self),allowed_mentions=discord.AllowedMentions.none())
        await self.bot.log_action(guild,"Verification entry published",f"Moderator {actor.id} · channel {channel.id} · message {message.id}.")
        return message.jump_url

    async def open_rules(self, interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            return await interaction.followup.send("Use verification inside the server.",ephemeral=True)
        config = await self.policy(interaction.guild.id)
        if not config["enabled"]:
            return await interaction.followup.send("Screening is disabled in this server.",ephemeral=True)
        if not config["require_agreement"]:
            return await self.respond_assessment(interaction)
        embed = discord.Embed(title="Server rules · read before agreeing",description=config["rules_text"],color=0xD8A7B1)
        await interaction.followup.send(embed=embed,view=AgreementView(self,interaction.guild.id,interaction.user.id,rules_revision(config)),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    async def respond_assessment(self, interaction, revision=None):
        try:
            text = await self.assess(interaction.guild,interaction.user.id,revision)
        except ValueError as exc:
            text = str(exc)
        except discord.HTTPException:
            text = "Discord could not complete verification. Your access was not confirmed; ask staff to check permissions and retry."
        await interaction.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


class ScreeningEntryView(discord.ui.View):
    def __init__(self, manager):
        super().__init__(timeout=None)
        self.manager = manager

    @discord.ui.button(label="Read rules & verify",style=discord.ButtonStyle.primary,custom_id="espresso:screening:entry:v1")
    async def verify(self, interaction, button):
        await self.manager.open_rules(interaction)


class AgreementView(discord.ui.View):
    def __init__(self, manager, guild_id, user_id, revision):
        super().__init__(timeout=180)
        self.manager,self.guild_id,self.user_id,self.revision = manager,guild_id,user_id,revision

    @discord.ui.button(label="I agree & request access",style=discord.ButtonStyle.success)
    async def agree(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id != self.guild_id or interaction.user.id != self.user_id:
            return await interaction.followup.send("Open your own verification request with /verify.",ephemeral=True)
        await self.manager.respond_assessment(interaction,self.revision)
