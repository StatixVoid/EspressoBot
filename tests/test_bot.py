from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord

from espresso.bot import Espresso, UserError, target_error


def make_member(guild, *, user_id=10, role=1, admin=False):
    member = Mock(spec=discord.Member)
    member.id = user_id
    member.bot = False
    member.guild = guild
    member.guild_permissions = SimpleNamespace(administrator=admin, moderate_members=False, manage_guild=False)
    member.top_role = role
    member.roles = []
    member.timed_out_until = None
    member.timeout = AsyncMock()
    member.send = AsyncMock()
    return member


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Espresso(":memory:")
        self.bot._connection.user = SimpleNamespace(id=99)
        await self.bot.store.open()
        self.guild = SimpleNamespace(id=1, owner_id=100, name="Test Server")
        self.guild.me = make_member(self.guild, user_id=99, role=10)
        self.guild.me.guild_permissions.moderate_members = True
        self.member = make_member(self.guild)
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        self.bot.log_action = AsyncMock(return_value=True)
        await self.bot.store.update(1, enabled=True, bad_words=["testword"])
        self.message = SimpleNamespace(
            guild=self.guild, author=self.member, content="testword", id=50, webhook_id=None,
            channel=SimpleNamespace(id=5), delete=AsyncMock(), is_system=lambda: False)

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_violation_deletes_warns_and_deduplicates_edit(self):
        await self.bot.moderate_message(self.message, edited=False)
        await self.bot.moderate_message(self.message, edited=True)
        self.assertEqual(self.message.delete.await_count, 2)
        self.assertEqual(len(await self.bot.store.warnings(1, 10)), 1)
        self.member.send.assert_awaited_once()

    async def test_mention_setup_requires_permission_and_limits_replies(self):
        self.message.content = "<@99> setup"
        self.message.channel.send = AsyncMock()
        await self.bot.on_message(self.message)
        self.assertIn("Manage Server",self.message.channel.send.await_args.args[0])
        self.message.delete.assert_not_awaited()
        self.message.channel.send.reset_mock()
        self.member.guild_permissions.manage_guild = True
        self.bot.setup_hint_times.clear()
        self.bot.website_base_url = "https://espresso.harperurias.com"
        await self.bot.on_message(self.message)
        self.assertIn("/setup?server=1",self.message.channel.send.await_args.args[0])
        self.assertFalse(self.message.channel.send.await_args.kwargs["allowed_mentions"].everyone)
        await self.bot.on_message(self.message)
        self.message.channel.send.assert_awaited_once()
        self.assertFalse(await self.bot.store.warnings(1,10))

    async def test_ordinary_mentions_still_pass_through_moderation(self):
        self.message.content = "<@99> testword"
        await self.bot.on_message(self.message)
        self.message.delete.assert_awaited_once()

    async def test_slash_setup_returns_private_server_link(self):
        self.bot.website_base_url = "https://espresso.harperurias.com"
        interaction = SimpleNamespace(guild_id=1,response=SimpleNamespace(send_message=AsyncMock()))
        group = self.bot.tree.get_command("espresso")
        await group.get_command("setup").callback(interaction)
        self.assertIn("/setup?server=1",interaction.response.send_message.await_args.args[0])
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_exempt_role_admin_and_disabled(self):
        self.member.roles = [SimpleNamespace(id=20)]
        await self.bot.store.edit_list(1, "exempt_roles", 20, True)
        await self.bot.moderate_message(self.message, edited=False)
        self.message.delete.assert_not_awaited()
        self.member.roles = []
        self.member.guild_permissions.administrator = True
        await self.bot.moderate_message(self.message, edited=False)
        self.message.delete.assert_not_awaited()
        self.member.guild_permissions.administrator = False
        await self.bot.store.update(1, enabled=False)
        await self.bot.moderate_message(self.message, edited=False)
        self.message.delete.assert_not_awaited()

    async def test_failed_delete_and_dm_do_not_prevent_warning(self):
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Forbidden")
        self.message.delete.side_effect = forbidden
        self.member.send.side_effect = forbidden
        await self.bot.moderate_message(self.message, edited=False)
        self.assertEqual(len(await self.bot.store.warnings(1, 10)), 1)
        self.assertIn("Deletion failed", self.bot.log_action.await_args_list[0].args[2])

    async def test_deleted_message_does_not_warn(self):
        self.message.delete.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Not Found")
        await self.bot.moderate_message(self.message, edited=False)
        self.assertFalse(await self.bot.store.warnings(1, 10))

    async def test_threshold_applies_timeout_and_preserves_longer_timeout(self):
        await self.bot.store.update(1, warn_threshold=1)
        await self.bot.moderate_message(self.message, edited=False)
        self.member.timeout.assert_awaited_once()
        self.member.timeout.reset_mock()
        self.member.timed_out_until = discord.utils.utcnow() + timedelta(hours=1)
        result = await self.bot.set_timeout(self.member, 10, "test")
        self.assertIn("longer timeout kept", result)
        self.member.timeout.assert_not_awaited()

    async def test_category_remove_only_issues_no_warning(self):
        await self.bot.store.edit_hate_filter(1,"misogyny",enabled=True,level=3,action="delete")
        self.message.content = "women belong in the kitchen"
        await self.bot.moderate_message(self.message,edited=False)
        self.message.delete.assert_awaited_once()
        self.assertFalse(await self.bot.store.warnings(1,10))

    async def test_direct_category_timeout_does_not_wait_for_warning_threshold(self):
        await self.bot.store.edit_hate_filter(1,"misogyny",enabled=True,level=3,action="timeout")
        await self.bot.store.update(1,warn_threshold=0)
        self.message.content = "women belong in the kitchen"
        await self.bot.moderate_message(self.message,edited=False)
        self.member.timeout.assert_awaited_once()
        self.assertEqual(len(await self.bot.store.warnings(1,10)),1)

    async def test_timeout_permissions_and_hierarchy(self):
        self.guild.me.guild_permissions.moderate_members = False
        self.assertIn("needs Moderate Members", await self.bot.set_timeout(self.member, 10, "test"))
        self.guild.me.guild_permissions.moderate_members = True
        self.member.top_role = 10
        self.assertIn("highest role", await self.bot.set_timeout(self.member, 10, "test"))
        self.member.timeout.assert_not_awaited()
        actor = make_member(self.guild, user_id=20, role=5)
        self.assertIn("Your highest role", target_error(actor, self.member, self.guild.me))

    async def test_timeout_api_failure_keeps_warning_and_reports_failure(self):
        await self.bot.store.update(1, warn_threshold=1)
        self.member.timeout.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Forbidden")
        await self.bot.moderate_message(self.message, edited=False)
        self.assertEqual(len(await self.bot.store.warnings(1, 10)), 1)
        self.assertIn("Timeout failed", self.member.send.await_args.args[0])

    async def test_group_permissions_enforced_when_ui_permissions_changed(self):
        interaction = SimpleNamespace(guild=self.guild, user=self.member)
        group = self.bot.tree.get_command("espresso")
        with self.assertRaises(UserError):
            await group.get_command("settings")._check_can_run(interaction)
        self.member.guild_permissions.manage_guild = True
        self.assertTrue(await group.get_command("settings")._check_can_run(interaction))
        interaction.guild = None
        with self.assertRaises(UserError):
            await group.interaction_check(interaction)

    async def test_slash_schema_and_minimal_intents(self):
        groups = self.bot.tree.get_commands()
        self.assertEqual({g.name for g in groups}, {"espresso", "mod", "verify", "screening"})
        for group in groups:
            schema = group.to_dict(self.bot.tree)
            self.assertFalse(schema["dm_permission"])
            if group.name != "verify":
                self.assertTrue(schema["default_member_permissions"])
        self.assertTrue(self.bot.intents.message_content)
        self.assertFalse(self.bot.intents.members)
        self.assertFalse(self.bot.intents.presences)

    async def test_raw_uncached_edit_is_fetched(self):
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=self.message))
        self.guild.get_channel_or_thread = Mock(return_value=channel)
        self.bot.get_guild = Mock(return_value=self.guild)
        payload = SimpleNamespace(guild_id=1, channel_id=5, message_id=50, data={"content": "testword"})
        await self.bot.on_raw_message_edit(payload)
        channel.fetch_message.assert_awaited_once_with(50)
        self.message.delete.assert_awaited_once()

    async def test_embed_only_edit_is_ignored(self):
        self.bot.get_guild = Mock()
        await self.bot.on_raw_message_edit(SimpleNamespace(guild_id=1, data={"embeds": []}))
        self.bot.get_guild.assert_not_called()

    async def test_edit_does_not_count_toward_spam(self):
        self.message.content = "hello"
        await self.bot.store.update(1, spam_limit=2)
        await self.bot.moderate_message(self.message, edited=False)
        for _ in range(3):
            await self.bot.moderate_message(self.message, edited=True)
        self.message.delete.assert_not_awaited()
        await self.bot.moderate_message(self.message, edited=False)
        self.message.delete.assert_awaited_once()
