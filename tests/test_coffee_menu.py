from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord

from espresso.coffee_menu import DRINKS, DRINK_BY_ID, CHARACTERS, PUBLIC_ASSETS, character_asset, character_choice, drink_choice
from espresso.community_commands import CoffeeMenuView
from espresso.community_config import default_community
from espresso.rules import Settings
import test_community as fixture


class CoffeeCatalogTests(unittest.TestCase):
    def test_real_beverage_catalog_and_safe_character_paths(self):
        self.assertEqual(set(DRINK_BY_ID),{"espresso","americano","cappuccino","flat-white","latte","mocha","caramel-latte","cold-brew","iced-latte","hot-chocolate","chai-latte","matcha-latte"})
        self.assertEqual(drink_choice("espresso")["description"],"an espresso")
        self.assertEqual(drink_choice("hot-chocolate")["caffeine"],"cocoa")
        self.assertEqual(drink_choice("chai-latte")["category"],"tea")
        self.assertIsNone(drink_choice("surprise"))
        self.assertEqual(len(CHARACTERS),5)
        for character in CHARACTERS:
            asset = character_asset(character["id"])
            if character["id"] == "none":
                self.assertIsNone(asset)
            else:
                self.assertEqual(asset.parent,PUBLIC_ASSETS)
                self.assertEqual("/assets/" + asset.name,character["image"])
        for bad in ("../.env","https://example.com/art.png",[],None,"unknown"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):drink_choice(bad)
                with self.assertRaises(ValueError):character_choice(bad)
                with self.assertRaises(ValueError):character_asset(bad)

    def test_existing_policies_inherit_character_default_and_reject_unknown_values(self):
        settings = Settings(community={"coffee_enabled":True,"coffee_title":"Our café"})
        self.assertEqual(settings.community["coffee_character"],"sabrina")
        self.assertEqual(settings.community["coffee_title"],"Our café")
        for invalid in ("../private.png","unknown",[],None,True):
            with self.subTest(value=invalid),self.assertRaises(ValueError):
                Settings(community=default_community() | {"coffee_character":invalid})


class CoffeeCardsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.CommunityManagerTests.asyncSetUp
    asyncTearDown = fixture.CommunityManagerTests.asyncTearDown

    def interaction(self):
        self.member.mention = "<@10>"
        self.actor.mention = "<@90>"
        return SimpleNamespace(guild=self.guild,guild_id=1,user=self.member,channel=self.channel,
                               response=SimpleNamespace(send_message=AsyncMock(),edit_message=AsyncMock(),defer=AsyncMock(),is_done=lambda:False),
                               edit_original_response=AsyncMock(),
                               followup=SimpleNamespace(send=AsyncMock()),message=SimpleNamespace(edit=AsyncMock()))

    async def enable(self, **changes):
        await self.bot.store.update(1,community=default_community() | {"coffee_enabled":True} | changes)

    async def test_selected_cocoa_card_has_local_art_and_no_notifications(self):
        await self.enable(coffee_style="custom",coffee_color="123456")
        interaction = self.interaction()
        await self.features.coffee(interaction,self.actor,"hot-chocolate","sabrina")
        sent = interaction.response.send_message.await_args.kwargs
        embed = sent["embed"]
        self.assertIn("<@10> brought <@90> a hot chocolate.",embed.description)
        self.assertEqual(embed.color.value,0x123456)
        self.assertIn("chocolate",embed.fields[0].value)
        self.assertIn("cocoa contains some caffeine",embed.fields[1].value)
        self.assertEqual(embed.fields[2].name,"Sabrina")
        self.assertEqual(embed.image.url,"attachment://sabrina-muse-v1.png")
        self.assertEqual(sent["file"].filename,"sabrina-muse-v1.png")
        self.assertTrue(sent["file"].fp.closed)
        self.assertFalse(sent["allowed_mentions"].users)
        self.assertFalse(sent["allowed_mentions"].roles)
        self.assertFalse(sent["allowed_mentions"].everyone)

    async def test_simple_card_works_without_attachment_permission(self):
        await self.enable(coffee_character="none")
        self.channel.permissions_for.return_value = discord.Permissions(view_channel=True,send_messages=True,embed_links=True)
        interaction = self.interaction()
        await self.features.coffee(interaction,self.actor,"iced-latte")
        sent = interaction.response.send_message.await_args.kwargs
        self.assertNotIn("file",sent)
        self.assertIsNone(sent["embed"].image.url)
        self.assertEqual(len(sent["embed"].fields),2)
        self.assertIn("Served over ice",sent["embed"].fields[1].value)

    async def test_failed_send_closes_artwork_and_keeps_retry_cooldown(self):
        await self.enable()
        interaction = self.interaction()
        interaction.response.send_message.side_effect = discord.Forbidden(SimpleNamespace(status=403,reason="Forbidden"),"Forbidden")
        with self.assertRaises(discord.Forbidden):
            await self.features.coffee(interaction,self.actor,"espresso","sabrina")
        attachment = interaction.response.send_message.await_args.kwargs["file"]
        self.assertTrue(attachment.fp.closed)
        with self.assertRaisesRegex(ValueError,"30 seconds"):
            await self.features.coffee(interaction,self.actor,"espresso","sabrina")
        self.assertEqual(interaction.response.send_message.await_count,1)

    async def test_bad_drink_character_and_target_do_not_consume_cooldown(self):
        await self.enable()
        interaction = self.interaction()
        for drink,character in (("../.env","sabrina"),("latte","../.env"),("unknown","none")):
            with self.subTest(drink=drink,character=character),self.assertRaises(ValueError):
                await self.features.coffee(interaction,self.actor,drink,character)
        with self.assertRaises(ValueError):
            await self.features.coffee(interaction,SimpleNamespace(guild=self.guild),"latte","none")
        self.assertEqual(self.features.command_cooldowns,{})
        await self.features.coffee(interaction,self.actor,"latte","none")
        interaction.response.send_message.assert_awaited_once()
        with self.assertRaisesRegex(ValueError,"30 seconds"):
            await self.features.coffee(interaction,self.actor,"mocha","none")
        interaction.response.send_message.assert_awaited_once()

    async def test_disabled_feature_and_lost_permissions_do_not_consume_cooldown(self):
        interaction = self.interaction()
        with self.assertRaises(ValueError):await self.features.coffee(interaction,self.actor,"latte","none")
        await self.enable()
        for permissions,error in ((discord.Permissions(view_channel=True),"permission to post"),
                                  (discord.Permissions(view_channel=True,send_messages=True),"Embed Links"),
                                  (discord.Permissions(view_channel=True,send_messages=True,embed_links=True),"Attach Files")):
            self.channel.permissions_for.return_value = permissions
            with self.subTest(error=error),self.assertRaisesRegex(ValueError,error):
                await self.features.coffee(interaction,self.actor,"latte","sabrina")
        self.assertEqual(self.features.command_cooldowns,{})
        interaction.response.send_message.assert_not_awaited()
        self.channel.permissions_for.return_value = discord.Permissions.all()
        await self.features.coffee(interaction,self.actor,"latte","none")
        interaction.response.send_message.assert_awaited_once()

    async def test_surprise_uses_the_whole_menu_and_override_wins_over_default(self):
        await self.enable(coffee_character="none")
        interaction = self.interaction()
        with patch("espresso.community.random.choice",return_value=DRINK_BY_ID["cold-brew"]) as choose:
            await self.features.coffee(interaction,self.actor,character="sabrina")
        choose.assert_called_once_with(DRINKS)
        sent = interaction.response.send_message.await_args.kwargs
        self.assertIn("a cold brew",sent["embed"].description)
        self.assertIn("Served over ice",sent["embed"].fields[1].value)
        self.assertEqual(sent["file"].filename,"sabrina-muse-v1.png")

    async def test_private_menu_rechecks_feature_and_permissions_before_send(self):
        await self.enable(coffee_character="none")
        interaction = self.interaction()
        await self.bot.tree.get_command("drinks").callback(interaction,self.actor)
        sent = interaction.followup.send.await_args.kwargs
        self.assertTrue(sent["ephemeral"])
        view = sent["view"]
        self.assertIsInstance(view,CoffeeMenuView)
        self.assertEqual(len(view.drink_select.options),13)
        await self.enable(coffee_enabled=False)
        clicked = self.interaction()
        await view.send_card(clicked)
        self.assertIn("off",clicked.response.send_message.await_args.args[0])
        self.assertTrue(clicked.response.send_message.await_args.kwargs["ephemeral"])
        self.assertFalse(view.sent)
        await self.enable(coffee_character="none")
        self.channel.permissions_for.return_value = discord.Permissions(view_channel=True)
        clicked = self.interaction()
        await view.send_card(clicked)
        self.assertIn("permission to post",clicked.response.send_message.await_args.args[0])
        self.assertEqual(self.features.command_cooldowns,{})
        self.channel.permissions_for.return_value = discord.Permissions.all()
        view.drink = "matcha-latte"
        clicked = self.interaction()
        await view.send_card(clicked)
        self.assertIn("a matcha latte",clicked.response.send_message.await_args.kwargs["embed"].description)
        self.assertTrue(view.sent)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        clicked.message.edit.assert_awaited_once_with(view=view)
        self.assertNotIn("ephemeral",clicked.response.send_message.await_args.kwargs)

    async def test_menu_ownership_and_duplicate_send_protection(self):
        await self.enable(coffee_character="none")
        view = CoffeeMenuView(self.features,10,1,self.actor,"none")
        other = self.interaction()
        other.user = self.actor
        self.assertFalse(await view.interaction_check(other))
        self.assertTrue(other.response.send_message.await_args.kwargs["ephemeral"])
        self.assertTrue(await view.interaction_check(self.interaction()))
        view.sent = True
        clicked = self.interaction()
        await view.send_card(clicked)
        self.assertTrue(clicked.response.send_message.await_args.kwargs["ephemeral"])
        self.assertEqual(self.features.command_cooldowns,{})
        view.stop()

    async def test_slash_menu_and_autocomplete_expose_supported_choices(self):
        coffee = self.bot.tree.get_command("coffee")
        parameters = {item.name:item for item in coffee.parameters}
        self.assertFalse(parameters["drink"].required)
        self.assertEqual({choice.value for choice in parameters["character"].choices},{item["id"] for item in CHARACTERS})
        autocomplete = coffee._params["drink"].autocomplete
        self.assertEqual([item.value for item in await autocomplete(self.interaction(),"chocolate")],["hot-chocolate"])
        self.assertEqual({item.value for item in await autocomplete(self.interaction(),"tea")},{"chai-latte","matcha-latte"})
        self.assertEqual(len(await autocomplete(self.interaction(),"")),13)
