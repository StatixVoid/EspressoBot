"""Private, short-lived beverage menus and explicitly saved usual orders."""

import logging
import sqlite3

import discord

from .coffee_menu import DRINKS, CHARACTERS, drink_choice, character_choice

LOG = logging.getLogger("espresso")


class CoffeeMenuView(discord.ui.View):
    """Saving a preference never posts a card or remembers its recipient."""

    def __init__(self, features, owner_id, guild_id, member, character, order=None):
        super().__init__(timeout=180)
        self.features, self.owner_id, self.guild_id, self.member = features, owner_id, guild_id, member
        self.usual = dict(order) if order else None
        self.drink = self.usual["drink"] if self.usual else "surprise"
        self.character = self.usual["character"] if self.usual else character
        drink_choice(self.drink)
        character_choice(self.character)
        self.sending = self.sent = self.preference_busy = False
        self.notice = "Your usual is loaded. You can change this order without changing your saved choice." if self.usual else ""
        self.drink_select = discord.ui.Select(placeholder="Choose your drink",row=0,options=[
            discord.SelectOption(label="Surprise me",value="surprise",description="Let the café choose your next sip",emoji="✨"),
            *(discord.SelectOption(label=drink["name"],value=drink["id"],description=drink["note"][:100],emoji=drink["emoji"]) for drink in DRINKS),
        ])
        self.character_select = discord.ui.Select(placeholder="Choose a café hero",row=1,options=[
            discord.SelectOption(label=item["name"],value=item["id"],description=item["tagline"][:100]) for item in CHARACTERS
        ])
        self.send_button = discord.ui.Button(label="Send card",style=discord.ButtonStyle.primary,emoji="☕",row=2)
        self.save_button = discord.ui.Button(label="Save as my usual",style=discord.ButtonStyle.secondary,row=3)
        self.load_button = discord.ui.Button(label="Load my usual",style=discord.ButtonStyle.secondary,row=3)
        self.clear_button = discord.ui.Button(label="Clear my usual",style=discord.ButtonStyle.secondary,row=3)
        for item,callback in ((self.drink_select,self.choose_drink),(self.character_select,self.choose_character),
                              (self.send_button,self.send_card),(self.save_button,self.save_usual),
                              (self.load_button,self.load_usual),(self.clear_button,self.clear_usual)):
            item.callback = callback
            self.add_item(item)
        self.refresh_choices()

    def refresh_choices(self):
        for option in self.drink_select.options:
            option.default = option.value == self.drink
        for option in self.character_select.options:
            option.default = option.value == self.character
        self.load_button.disabled = self.clear_button.disabled = self.usual is None

    def embed(self):
        selected, hero = drink_choice(self.drink), character_choice(self.character)
        description = "Pick a drink and a café companion, then send a little kindness.\nYour card will appear in this channel, without a notification ping."
        if self.notice:
            description += "\n\n" + self.notice
        embed = discord.Embed(title="Espresso's beverage menu",description=description,color=0xC9B5EB)
        for category,label in (("coffee","☕ Coffee"),("cocoa","🍫 Cocoa"),("tea","🍵 Tea")):
            embed.add_field(name=label,value=" · ".join(item["name"] for item in DRINKS if item["category"] == category),inline=False)
        embed.add_field(name="Your order",value=(f"**{selected['name']}** — {selected['note']}" if selected else "**Surprise me** — a random drink from the whole menu.") + f"\nCompanion: **{hero['name']}**",inline=False)
        usual = "No usual saved. Save your drink and companion for your next visit; this sends nothing."
        if self.usual:
            drink = drink_choice(self.usual["drink"])
            usual = f"**{drink['name'] if drink else 'Surprise me'}** · {character_choice(self.usual['character'])['name']}\nPrivate to you in this server. The recipient is never saved."
        embed.add_field(name="Your usual in this server",value=usual,inline=False)
        embed.set_footer(text="This menu closes after 3 minutes · coffee cards share a 30-second cooldown")
        return embed

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Open your own menu with /drinks to make an order.",ephemeral=True)
            return False
        return True

    async def available(self, interaction):
        if not await self.interaction_check(interaction):
            return False
        if self.sent:
            await interaction.response.send_message("This order has been served. Open /drinks for another.",ephemeral=True)
            return False
        if self.sending or self.preference_busy:
            await interaction.response.send_message("Please wait for your current café action to finish.",ephemeral=True)
            return False
        return True

    async def choose_drink(self, interaction):
        if not await self.available(interaction):
            return
        value = self.drink_select.values[0]
        drink_choice(value)
        self.drink, self.notice = value, ""
        self.refresh_choices()
        await interaction.response.edit_message(embed=self.embed(),view=self)

    async def choose_character(self, interaction):
        if not await self.available(interaction):
            return
        value = self.character_select.values[0]
        character_choice(value)
        self.character, self.notice = value, ""
        self.refresh_choices()
        await interaction.response.edit_message(embed=self.embed(),view=self)

    async def save_usual(self, interaction):
        await self.change_usual(interaction,"save")

    async def load_usual(self, interaction):
        await self.change_usual(interaction,"load")

    async def clear_usual(self, interaction):
        await self.change_usual(interaction,"clear")

    async def change_usual(self, interaction, operation):
        if not await self.available(interaction):
            return
        self.preference_busy = True
        try:
            # Acknowledge before database access; only the existing private menu is edited.
            await interaction.response.defer()
            if operation == "save":
                settings = await self.features.store.settings(self.guild_id)
                if not settings.community["coffee_enabled"]:
                    raise ValueError("Coffee cards are off. You can still clear your saved usual with /drinks forget:True.")
                await self.features.store.save_coffee_order(self.guild_id,self.owner_id,self.drink,self.character)
                self.usual = {"drink":self.drink,"character":self.character}
                self.notice = "Saved as your usual in this server. No card was sent."
            elif operation == "clear":
                await self.features.store.clear_coffee_order(self.guild_id,self.owner_id)
                self.usual = None
                self.notice = "Your saved usual is cleared. Your current order is still here; no card was sent."
            elif operation == "load":
                self.usual = await self.features.store.coffee_order(self.guild_id,self.owner_id)
                if self.usual:
                    self.drink, self.character = self.usual["drink"], self.usual["character"]
                    self.notice = "Loaded your current saved usual. No card was sent."
                else:
                    self.notice = "No available usual is saved. Your current choice is unchanged."
            self.refresh_choices()
            await interaction.edit_original_response(embed=self.embed(),view=self)
        except ValueError as error:
            await interaction.followup.send(str(error),ephemeral=True)
        except (sqlite3.Error,OSError):
            await interaction.followup.send("The saved-order change could not be confirmed. Open /drinks again to check your usual before retrying.",ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("The menu update could not be confirmed. Open /drinks again to check your saved usual.",ephemeral=True)
        finally:
            self.preference_busy = False

    async def send_card(self, interaction):
        if not await self.available(interaction):
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

    async def on_error(self, interaction, error, item):
        LOG.warning("Café menu update failed (%s).",type(error).__name__)
        message = "This menu could not be updated. Open /drinks again to check your order."
        if interaction.response.is_done():
            await interaction.followup.send(message,ephemeral=True)
        else:
            await interaction.response.send_message(message,ephemeral=True)
