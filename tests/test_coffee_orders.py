import asyncio
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord

from espresso.community_commands import CoffeeMenuView
import test_coffee_menu as fixture


class CoffeeOrderTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.CoffeeCardsTests.asyncSetUp
    asyncTearDown = fixture.CoffeeCardsTests.asyncTearDown
    interaction = fixture.CoffeeCardsTests.interaction
    enable = fixture.CoffeeCardsTests.enable

    async def open_menu(self, member=None):
        interaction = self.interaction()
        await self.bot.tree.get_command("drinks").callback(interaction,member)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        payload = interaction.followup.send.await_args.kwargs
        self.assertTrue(payload["ephemeral"])
        self.assertFalse(payload["allowed_mentions"].everyone)
        view = payload["view"]
        self.addCleanup(view.stop)
        return view

    async def test_saved_usual_loads_for_the_sender_but_never_restores_recipient(self):
        await self.enable(coffee_character="sabrina")
        await self.bot.store.save_coffee_order(1,10,"hot-chocolate","dreamer")
        await self.bot.store.save_coffee_order(1,90,"espresso","none")
        view = await self.open_menu(self.actor)
        self.assertEqual((view.drink,view.character),("hot-chocolate","dreamer"))
        self.assertIs(view.member,self.actor)
        self.assertEqual([option.value for option in view.drink_select.options if option.default],["hot-chocolate"])
        self.assertEqual([option.value for option in view.character_select.options if option.default],["dreamer"])
        self.assertFalse(view.clear_button.disabled)
        self.assertIs((await self.open_menu()).member,self.member)
        self.assertEqual((await self.bot.store.coffee_order(1,90))["drink"],"espresso")

    async def test_saving_is_private_without_posting_or_consuming_cooldown(self):
        await self.enable()
        view = await self.open_menu(self.actor)
        view.drink,view.character = "matcha-latte","none"
        interaction = self.interaction()
        with patch.object(self.features,"coffee",new_callable=AsyncMock) as coffee:
            await view.save_usual(interaction)
            coffee.assert_not_awaited()
        self.assertEqual(await self.bot.store.coffee_order(1,10),{"drink":"matcha-latte","character":"none"})
        self.channel.send.assert_not_awaited()
        interaction.response.send_message.assert_not_awaited()
        interaction.response.defer.assert_awaited_once()
        interaction.edit_original_response.assert_awaited_once()
        self.assertIn("No card was sent",view.embed().description)
        self.assertEqual(self.features.command_cooldowns,{})
        self.assertIsNone(await self.bot.store.coffee_order(1,90))

    async def test_load_reads_the_current_saved_order_without_overwriting_it(self):
        await self.enable()
        await self.bot.store.save_coffee_order(1,10,"latte","guardian")
        view = await self.open_menu()
        view.drink,view.character = "cold-brew","none"
        await self.bot.store.save_coffee_order(1,10,"chai-latte","spark")
        await view.load_usual(self.interaction())
        self.assertEqual((view.drink,view.character),("chai-latte","spark"))
        self.assertEqual(self.features.command_cooldowns,{})
        await self.bot.store.clear_coffee_order(1,10)
        await view.load_usual(self.interaction())
        self.assertEqual((view.drink,view.character),("chai-latte","spark"))
        self.assertTrue(view.load_button.disabled)
        self.assertIn("current choice is unchanged",view.embed().description)

    async def test_clear_works_after_cards_are_disabled_and_keeps_current_choice(self):
        await self.enable()
        await self.bot.store.save_coffee_order(1,10,"mocha","guardian")
        view = await self.open_menu()
        await self.enable(coffee_enabled=False)
        interaction = self.interaction()
        await view.clear_usual(interaction)
        self.assertIsNone(await self.bot.store.coffee_order(1,10))
        self.assertEqual((view.drink,view.character),("mocha","guardian"))
        self.assertTrue(view.clear_button.disabled)
        self.assertIn("cleared",view.embed().description)
        self.channel.send.assert_not_awaited()
        await self.enable(coffee_character="none")
        fresh = await self.open_menu()
        self.assertEqual((fresh.drink,fresh.character),("surprise","none"))

    async def test_other_member_or_server_cannot_change_or_load_a_preference(self):
        await self.enable()
        await self.bot.store.save_coffee_order(1,10,"espresso","none")
        view = await self.open_menu()
        for operation in (view.save_usual,view.clear_usual,view.load_usual):
            for other_member in (True,False):
                interaction = self.interaction()
                if other_member:
                    interaction.user = self.actor
                else:
                    interaction.guild_id = 2
                await operation(interaction)
                self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
                interaction.response.defer.assert_not_awaited()
        self.assertEqual(await self.bot.store.coffee_order(1,10),{"drink":"espresso","character":"none"})

    async def test_save_rechecks_disabled_policy_and_validation_without_losing_usual(self):
        await self.enable()
        await self.bot.store.save_coffee_order(1,10,"latte","none")
        view = await self.open_menu()
        view.drink = "hot-chocolate"
        await self.enable(coffee_enabled=False)
        interaction = self.interaction()
        await view.save_usual(interaction)
        self.assertIn("cards are off",interaction.followup.send.await_args.args[0])
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        await self.enable()
        view.drink = "../private"
        await view.save_usual(self.interaction())
        self.assertEqual(await self.bot.store.coffee_order(1,10),{"drink":"latte","character":"none"})
        self.assertEqual(self.features.command_cooldowns,{})

    async def test_forget_command_clears_only_self_even_when_cards_and_posting_are_off(self):
        await self.enable(coffee_enabled=False)
        await self.bot.store.save_coffee_order(1,10,"latte","none")
        await self.bot.store.save_coffee_order(1,90,"mocha","spark")
        self.channel.permissions_for.return_value = discord.Permissions.none()
        interaction = self.interaction()
        await self.bot.tree.get_command("drinks").callback(interaction,self.actor,True)
        self.assertIsNone(await self.bot.store.coffee_order(1,10))
        self.assertEqual((await self.bot.store.coffee_order(1,90))["drink"],"mocha")
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        self.channel.send.assert_not_awaited()
        parameters = {parameter.name:parameter for parameter in self.bot.tree.get_command("drinks").parameters}
        self.assertFalse(parameters["forget"].required)

    async def test_forget_requires_server_member_context(self):
        await self.bot.store.save_coffee_order(1,10,"latte","none")
        for mismatch in (False,True):
            interaction = self.interaction()
            if mismatch:
                interaction.guild_id = 2
            else:
                interaction.guild = None
            with self.assertRaisesRegex(ValueError,"server"):
                await self.bot.tree.get_command("drinks").callback(interaction,None,True)
        self.assertIsNotNone(await self.bot.store.coffee_order(1,10))

    async def test_retired_saved_choices_use_server_defaults_without_breaking_menu(self):
        await self.enable(coffee_character="spark")
        async with self.bot.store.lock:
            await self.bot.store.db.execute("INSERT INTO coffee_preferences VALUES (1,10,'old-drink','old-art')")
            await self.bot.store.db.commit()
        view = await self.open_menu()
        self.assertEqual((view.drink,view.character),("surprise","spark"))
        self.assertTrue(view.clear_button.disabled)
        await self.bot.tree.get_command("drinks").callback(self.interaction(),None,True)
        async with self.bot.store.db.execute("SELECT COUNT(*) FROM coffee_preferences") as cursor:
            self.assertEqual((await cursor.fetchone())[0],0)

    async def test_slow_save_is_acknowledged_and_blocks_overlapping_menu_actions(self):
        await self.enable()
        view = await self.open_menu()
        view.drink = "hot-chocolate"
        entered,release = asyncio.Event(),asyncio.Event()
        original = self.bot.store.save_coffee_order
        async def slow_save(*arguments):
            entered.set()
            await release.wait()
            await original(*arguments)
        interaction = self.interaction()
        with patch.object(self.bot.store,"save_coffee_order",side_effect=slow_save) as saving:
            task = asyncio.create_task(view.save_usual(interaction))
            try:
                await entered.wait()
                interaction.response.defer.assert_awaited_once()
                for operation in (view.save_usual,view.clear_usual,view.load_usual,view.send_card,view.choose_drink):
                    blocked = self.interaction()
                    await operation(blocked)
                    self.assertIn("Please wait",blocked.response.send_message.await_args.args[0])
                self.assertEqual(saving.await_count,1)
            finally:
                release.set()
                await task
        self.assertEqual((await self.bot.store.coffee_order(1,10))["drink"],"hot-chocolate")
        self.assertEqual(self.features.command_cooldowns,{})

    async def test_storage_failure_does_not_claim_saved_or_expose_private_error(self):
        await self.enable()
        view = await self.open_menu()
        interaction = self.interaction()
        with patch.object(self.bot.store,"save_coffee_order",side_effect=sqlite3.OperationalError("PRIVATE_DB_PATH")):
            await view.save_usual(interaction)
        message = interaction.followup.send.await_args.args[0]
        self.assertNotIn("PRIVATE_DB_PATH",message)
        self.assertIn("could not be confirmed",message)
        self.assertIsNone(view.usual)
        self.assertFalse(view.preference_busy)
        self.assertIsNone(await self.bot.store.coffee_order(1,10))

    async def test_lost_menu_update_does_not_replay_a_successful_preference_write(self):
        await self.enable()
        view = await self.open_menu()
        view.drink = "flat-white"
        interaction = self.interaction()
        interaction.edit_original_response.side_effect = discord.HTTPException(SimpleNamespace(status=503,reason="Unavailable"),"private failure")
        with patch.object(self.bot.store,"save_coffee_order",wraps=self.bot.store.save_coffee_order) as saving:
            await view.save_usual(interaction)
            self.assertEqual(saving.await_count,1)
        self.assertEqual((await self.bot.store.coffee_order(1,10))["drink"],"flat-white")
        self.assertIn("Open /drinks again",interaction.followup.send.await_args.args[0])
        self.assertFalse(view.preference_busy)

    async def test_served_menu_refuses_late_preference_changes(self):
        await self.enable(coffee_character="none")
        view = await self.open_menu()
        await view.send_card(self.interaction())
        for operation in (view.save_usual,view.clear_usual,view.load_usual):
            interaction = self.interaction()
            await operation(interaction)
            self.assertIn("served",interaction.response.send_message.await_args.args[0])
        self.assertIsNone(await self.bot.store.coffee_order(1,10))

    async def test_bad_component_value_does_not_poison_current_selection(self):
        await self.enable()
        view = await self.open_menu()
        view.drink_select._values = ["../bad"]
        with self.assertRaises(ValueError):
            await view.choose_drink(self.interaction())
        self.assertEqual(view.drink,"surprise")
        with self.assertLogs("espresso",level="WARNING") as logs:
            await view.on_error(self.interaction(),sqlite3.Error("PRIVATE_PATH"),view.save_button)
        self.assertNotIn("PRIVATE_PATH"," ".join(logs.output))
