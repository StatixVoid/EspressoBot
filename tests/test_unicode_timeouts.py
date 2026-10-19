from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import discord

import test_bot as fixtures


class UnicodeTimeoutTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.BotTests.asyncSetUp
    asyncTearDown=fixtures.BotTests.asyncTearDown

    async def enable_filter(self):
        await self.bot.store.edit_hate_filter(1,"trans_hate",enabled=True,level=5,action="delete")
        await self.bot.store.update(1,warn_threshold=0)
        self.message.content="τrаnոу"

    async def test_unicode_disguise_times_out_even_when_action_is_delete_and_threshold_is_zero(self):
        await self.enable_filter()
        await self.bot.moderate_message(self.message,edited=False)
        self.member.timeout.assert_awaited_once()
        self.assertEqual(len(await self.bot.store.warnings(1,10)),1)
        cases=await self.bot.store.cases(1)
        self.assertEqual(sum(case["kind"]=="timeout" for case in cases),1)
        self.assertTrue(any("Timed out" in case["outcome"] for case in cases))

    async def test_warning_cooldown_does_not_prevent_timeout_and_duplicate_message_cannot_repeat_it(self):
        await self.bot.moderate_message(self.message,edited=False)
        await self.enable_filter()
        self.message.id=51
        await self.bot.moderate_message(self.message,edited=False)
        self.member.timeout.assert_awaited_once()
        self.assertEqual(len(await self.bot.store.warnings(1,10)),1)
        await self.bot.moderate_message(self.message,edited=True)
        self.member.timeout.assert_awaited_once()
        self.assertEqual(sum(case["kind"]=="timeout" for case in await self.bot.store.cases(1)),1)

    async def test_edit_of_previously_warned_message_can_trigger_its_first_unicode_timeout(self):
        await self.bot.moderate_message(self.message,edited=False)
        await self.enable_filter()
        await self.bot.moderate_message(self.message,edited=True)
        self.member.timeout.assert_awaited_once()
        self.assertEqual(len(await self.bot.store.warnings(1,10)),1)

    async def test_self_deleted_unicode_abuse_is_still_handled_once(self):
        await self.enable_filter()
        self.message.delete.side_effect=discord.NotFound(SimpleNamespace(status=404,reason="Not Found"),"Not Found")
        await self.bot.moderate_message(self.message,edited=False)
        await self.bot.moderate_message(self.message,edited=True)
        self.member.timeout.assert_awaited_once()

    async def test_hierarchy_and_longer_existing_timeouts_remain_protected(self):
        await self.enable_filter();self.member.top_role=10
        await self.bot.moderate_message(self.message,edited=False)
        self.member.timeout.assert_not_awaited()
        self.assertTrue(any("highest role" in case["outcome"] for case in await self.bot.store.cases(1)))

    async def test_observation_exemptions_and_plain_unicode_do_not_trigger_actions(self):
        await self.enable_filter();await self.bot.store.update(1,observation_mode=True)
        await self.bot.moderate_message(self.message,edited=False)
        self.member.timeout.assert_not_awaited();self.message.delete.assert_not_awaited()
        await self.bot.store.update(1,observation_mode=False)
        self.message.content="José supports trans people 🏳️‍⚧️"
        await self.bot.moderate_message(self.message,edited=False)
        self.member.timeout.assert_not_awaited();self.message.delete.assert_not_awaited()
        self.message.content="τrаnոу";self.member.guild_permissions.administrator=True
        await self.bot.moderate_message(self.message,edited=False)
        self.member.timeout.assert_not_awaited();self.message.delete.assert_not_awaited()

    async def test_a_pending_timeout_case_cannot_be_replayed_after_recovery(self):
        await self.enable_filter()
        case_id,claimed=await self.bot.store.claim_unicode_timeout(1,10,99,"Unicode evasion","message:50")
        self.assertTrue(claimed)
        again,repeated=await self.bot.store.claim_unicode_timeout(1,10,99,"Unicode evasion","message:50")
        self.assertEqual(again,case_id);self.assertFalse(repeated)
        await self.bot.moderate_message(self.message,edited=False)
        self.member.timeout.assert_not_awaited()

    async def test_reported_ascii_bracket_bypass_is_enforced_with_the_selected_action(self):
        await self.enable_filter();self.message.content="Tr(an)y"
        await self.bot.moderate_message(self.message,edited=False)
        self.message.delete.assert_awaited_once();self.member.timeout.assert_not_awaited()
        self.assertEqual(await self.bot.store.warnings(1,10),[])
        await self.bot.store.edit_hate_filter(1,"trans_hate",action="timeout")
        self.message.id=51
        await self.bot.moderate_message(self.message,edited=False)
        self.member.timeout.assert_awaited_once()

    async def test_unicode_bracket_bypass_retains_immediate_timeout_during_warning_cooldown(self):
        await self.bot.store.warn(1,10,99,"Earlier warning","message:49",24,automatic=True)
        await self.enable_filter();self.message.content="tr(аո)y"
        await self.bot.moderate_message(self.message,edited=False)
        self.message.delete.assert_awaited_once();self.member.timeout.assert_awaited_once()
        self.assertEqual(len(await self.bot.store.warnings(1,10)),1)

    async def test_ascii_symbol_mask_uses_the_selected_delete_action(self):
        await self.enable_filter();self.message.content="tr*nny"
        await self.bot.moderate_message(self.message,edited=False)
        self.message.delete.assert_awaited_once();self.member.timeout.assert_not_awaited()
        self.assertEqual(await self.bot.store.warnings(1,10),[])

    async def test_emoji_mask_times_out_during_cooldown_and_only_once_per_message(self):
        await self.bot.store.warn(1,10,99,"Earlier warning","message:49",24,automatic=True)
        await self.enable_filter();self.message.content="tr💜nny"
        await self.bot.moderate_message(self.message,edited=False)
        self.message.delete.assert_awaited_once();self.member.timeout.assert_awaited_once()
        self.assertEqual(len(await self.bot.store.warnings(1,10)),1)
        await self.bot.moderate_message(self.message,edited=True)
        self.member.timeout.assert_awaited_once()
