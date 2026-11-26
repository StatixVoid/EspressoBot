"""Private pilot status and moderation reports; a public self-ID helper."""

from typing import Literal

import discord
from discord import app_commands


class PremiumGroup(app_commands.Group):
    async def interaction_check(self, interaction):
        from .bot import UserError
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise UserError("Use this command in a server.")
        permissions = interaction.user.guild_permissions
        if not (permissions.administrator or permissions.manage_guild or permissions.moderate_members):
            raise UserError("You need Manage Server or Moderate Members to use Premium Insights.")
        return True


def register_premium_commands(bot):
    @bot.tree.command(name="myid", description="Privately show your Discord user ID and this server's ID")
    @app_commands.guild_only()
    async def myid(interaction: discord.Interaction):
        text = f"Your user ID: `{interaction.user.id}`"
        if interaction.guild_id:
            text += f"\nServer ID: `{interaction.guild_id}`"
        await interaction.response.send_message(text, ephemeral=True)

    premium = PremiumGroup(name="premium", description="Espresso Premium pilot status and insights",
                           guild_only=True,
                           default_permissions=discord.Permissions(manage_guild=True, moderate_members=True))

    @premium.command(name="status", description="Check Premium pilot access for you and this server")
    async def status(interaction: discord.Interaction):
        access = bot.premium.access(interaction.guild_id, interaction.user.id)
        text = ("☕ Premium pilot access is active. Try `/premium insights`." if access["active"] else
                "Premium Insights is in an allowlisted pilot. This server and account do not have access yet.")
        await interaction.response.send_message(text, ephemeral=True)

    @premium.command(name="insights", description="Summarize recorded moderation cases over 7 or 30 UTC dates")
    async def insights(interaction: discord.Interaction, days: Literal[7, 30] = 7):
        from .bot import UserError
        if not bot.premium.access(interaction.guild_id, interaction.user.id)["active"]:
            raise UserError("Premium Insights requires pilot access for this server or your staff account.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        report = await bot.store.moderation_insights(interaction.guild_id, days)
        totals = report["totals"]
        embed = discord.Embed(title=f"☕ Premium Insights · {days} UTC dates", color=0xD8A7B1)
        for key, label in (("cases", "Recorded cases"), ("warnings", "Warnings recorded"),
                           ("timeouts", "Timeout cases"), ("members", "Members in recorded cases")):
            embed.add_field(name=label, value=str(totals[key]))
        embed.description = "Based on stored moderation cases. Today is partial; warnings include later-voided records. Timeout cases may include failed or pending attempts."
        embed.set_footer(text="Pilot feature · no billing enabled · not a measure of all server activity")
        await interaction.followup.send(embed=embed, ephemeral=True)

    bot.tree.add_command(premium)
