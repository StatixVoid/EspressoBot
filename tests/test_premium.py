from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer
import discord

from espresso.bot import Espresso, UserError
from espresso.config import WebConfig
from espresso.premium import PremiumConfig
from espresso.store import Store
from espresso.web import COOKIE, Panel, Session


GUILD = 1432165456684519587
USER = 926272518275362827


class PremiumConfigTests(unittest.TestCase):
    def test_project_defaults_and_sources(self):
        with patch.dict(os.environ, {}, clear=True):
            config = PremiumConfig.from_environment()
        self.assertEqual(config.access(GUILD, 10)["source"], "server_allowlist")
        self.assertEqual(config.access(1, USER)["source"], "user_allowlist")
        self.assertFalse(config.access(1, 10)["active"])
        self.assertFalse(config.access(GUILD, USER)["billing_enabled"])

    def test_explicit_empty_lists_remove_defaults(self):
        with patch.dict(os.environ, {"PREMIUM_GUILD_IDS":"", "PREMIUM_USER_IDS":""}, clear=True):
            self.assertFalse(PremiumConfig.from_environment().access(GUILD, USER)["active"])

    def test_disable_overrides_both_lists(self):
        with patch.dict(os.environ, {"PREMIUM_ENABLED":"false"}, clear=True):
            self.assertEqual(PremiumConfig.from_environment().access(GUILD, USER)["features"], [])

    def test_environment_replaces_defaults_and_deduplicates(self):
        with patch.dict(os.environ, {"PREMIUM_GUILD_IDS":"2, 2,3", "PREMIUM_USER_IDS":"20"}, clear=True):
            config = PremiumConfig.from_environment()
        self.assertFalse(config.access(GUILD, USER)["active"])
        self.assertTrue(config.access(2, 1)["active"])
        self.assertTrue(config.access(1, 20)["active"])

    def test_invalid_environment_is_rejected_without_echoing_values(self):
        for name, value in [("PREMIUM_ENABLED", "secret-value"),
                            *[("PREMIUM_GUILD_IDS", v) for v in
                              ("secret-value", "-1", "0", str(2**64), "١٢", "1.5")]]:
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name:value}, clear=True):
                with self.assertRaises(ValueError) as caught:
                    PremiumConfig.from_environment()
                self.assertIn(name, str(caught.exception))
                self.assertNotIn(value, str(caught.exception))


class PremiumStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        await self.store.open()
        self.now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc).timestamp()

    async def asyncTearDown(self):
        await self.store.close()

    async def case(self, guild, member, kind, timestamp, source):
        with patch("espresso.community_store.time.time", return_value=timestamp):
            await self.store.record_case(guild, member, 77, kind, "PRIVATE REASON", "PRIVATE OUTCOME", source)

    async def test_utc_boundaries_guild_isolation_and_no_private_content(self):
        start = datetime(2026, 11, 20, tzinfo=timezone.utc).timestamp()
        for guild, member, kind, timestamp, source in [
            (GUILD, 10, "warning", start-1, "outside"),
            (GUILD, 10, "warning", start, "start"),
            (GUILD, 10, "timeout", self.now, "now"),
            (GUILD, 20, "timeout_removed", self.now-1, "other"),
            (GUILD, 30, "timeout", self.now+1, "future"),
            (2, 90, "warning", self.now, "other-server"),
        ]:
            await self.case(guild, member, kind, timestamp, source)
        result = await self.store.moderation_insights(GUILD, 7, now=self.now)
        self.assertEqual(result["totals"], {"cases":3, "members":2, "warnings":1, "timeouts":1})
        self.assertEqual(result["daily"][0], {"date":"2026-11-20", "cases":1})
        self.assertEqual(result["daily"][-1], {"date":"2026-11-26", "cases":2})
        self.assertEqual(sum(row["cases"] for row in result["daily"]), 3)
        self.assertEqual(result["daily"][1]["cases"], 0)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(set(result["totals"]), {"cases","members","warnings","timeouts"})

    async def test_thirty_dates_and_empty_server_zero_fill(self):
        await self.case(GUILD, None, "warnings_cleared", self.now-20*86400, "old")
        self.assertEqual((await self.store.moderation_insights(GUILD, 7, now=self.now))["totals"]["cases"], 0)
        report = await self.store.moderation_insights(GUILD, 30, now=self.now)
        self.assertEqual(len(report["daily"]), 30)
        self.assertEqual(report["totals"]["cases"], 1)
        self.assertEqual(report["totals"]["members"], 0)
        empty = await self.store.moderation_insights(2, 30, now=self.now)
        self.assertEqual(empty["totals"]["cases"], 0)
        self.assertTrue(all(row["cases"] == 0 for row in empty["daily"]))

    async def test_guild_removal_erases_report_data(self):
        await self.case(GUILD, 10, "warning", self.now, "one")
        await self.store.delete_guild(GUILD)
        self.assertEqual((await self.store.moderation_insights(GUILD, now=self.now))["totals"]["cases"], 0)

    async def test_unsupported_periods_fail(self):
        for days in (True, 0, 1, 31, "7", 7.0):
            with self.assertRaises(ValueError):
                await self.store.moderation_insights(GUILD, days)


class PremiumWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        await self.store.open()
        self.api = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
                                   guilds=AsyncMock(return_value=[{"id":str(GUILD),"name":"Pilot","permissions":"32"}]))
        self.guild = SimpleNamespace(id=GUILD, unavailable=False)
        self.bot = SimpleNamespace(is_ready=Mock(return_value=True),
                                   get_guild=Mock(side_effect=lambda value: self.guild if value == GUILD else None))
        self.panel = Panel(WebConfig(), self.store, self.bot, self.api, premium=PremiumConfig())
        self.client = TestClient(TestServer(self.panel.app))
        await self.client.start_server()
        self.session = Session("PRIVATE TOKEN", {"id":str(USER)}, "csrf", time.time()+300)
        self.panel.sessions["test"] = self.session
        self.client.session.cookie_jar.update_cookies({COOKIE:"test"})
        self.base = f"/api/servers/{GUILD}/premium"

    async def asyncTearDown(self):
        await self.client.close()
        await self.store.close()

    async def test_pilot_reads_are_private_and_server_permission_is_fresh(self):
        response = await self.client.get(self.base)
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["active"])
        report = await self.client.get(self.base+"/insights?days=30")
        self.assertEqual(report.headers["Cache-Control"], "no-store")
        self.assertEqual((await report.json())["guild_id"], str(GUILD))
        self.assertEqual(self.api.guilds.await_count, 2)
        self.api.guilds.return_value[0]["permissions"] = "0"
        self.assertEqual((await self.client.get(self.base)).status, 403)
        self.assertEqual((await self.client.get(self.base+"/insights")).status, 403)

    async def test_allowlisted_user_cannot_read_nonmember_server(self):
        self.api.guilds.return_value = []
        self.assertEqual((await self.client.get(self.base+"/insights")).status, 403)

    async def test_unlisted_account_and_server_denied_even_with_client_flag(self):
        self.panel.premium = PremiumConfig(guild_ids=frozenset(), user_ids=frozenset())
        self.assertFalse((await (await self.client.get(self.base)).json())["active"])
        self.assertEqual((await self.client.get(self.base+"/insights?premium=true")).status, 403)

    async def test_independent_server_and_user_grants_and_disable(self):
        for config in (PremiumConfig(user_ids=frozenset()), PremiumConfig(guild_ids=frozenset())):
            self.panel.premium = config
            self.assertEqual((await self.client.get(self.base+"/insights")).status, 200)
        self.panel.premium = PremiumConfig(enabled=False)
        self.assertEqual((await self.client.get(self.base+"/insights")).status, 403)

    async def test_moderator_read_access_without_manager_permission(self):
        self.api.guilds.return_value[0]["permissions"] = str(1 << 40)
        self.assertEqual((await self.client.get(self.base+"/insights")).status, 200)

    async def test_expired_session_bot_offline_and_missing_install(self):
        self.bot.is_ready.return_value = False
        self.assertEqual((await self.client.get(self.base+"/insights")).status, 503)
        self.bot.is_ready.return_value = True
        self.bot.get_guild.return_value = None
        self.bot.get_guild.side_effect = None
        self.assertEqual((await self.client.get(self.base+"/insights")).status, 404)
        self.session.expires = time.time()-1
        self.assertEqual((await self.client.get(self.base+"/insights")).status, 401)

    async def test_invalid_report_periods_and_anonymous_access(self):
        for value in ("0", "31", "-7", "7.0", "secret"):
            self.assertEqual((await self.client.get(self.base+"/insights?days="+value)).status, 400)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get(self.base)).status, 401)


class PremiumCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Espresso(":memory:", premium=PremiumConfig())
        await self.bot.store.open()
        member = Mock(spec=discord.Member)
        member.id = USER
        member.guild_permissions = SimpleNamespace(administrator=False, manage_guild=False, moderate_members=False)
        self.interaction = SimpleNamespace(user=member, guild=SimpleNamespace(id=GUILD), guild_id=GUILD,
                                           response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                                           followup=SimpleNamespace(send=AsyncMock()))
        self.group = self.bot.tree.get_command("premium")

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_runtime_staff_check_cannot_be_bypassed_with_allowlist(self):
        command = self.group.get_command("insights")
        with self.assertRaises(UserError):
            await command._check_can_run(self.interaction)
        for name in ("manage_guild", "moderate_members", "administrator"):
            setattr(self.interaction.user.guild_permissions, name, True)
            self.assertTrue(await command._check_can_run(self.interaction))
            setattr(self.interaction.user.guild_permissions, name, False)
        self.interaction.guild = None
        with self.assertRaises(UserError):
            await self.group.interaction_check(self.interaction)

    async def test_private_report_and_rejected_entitlement(self):
        await self.group.get_command("insights").callback(self.interaction, 30)
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        result = self.interaction.followup.send.await_args.kwargs
        self.assertTrue(result["ephemeral"])
        self.assertIn("30 UTC dates", result["embed"].title)
        self.bot.premium = PremiumConfig(enabled=False)
        with self.assertRaises(UserError):
            await self.group.get_command("insights").callback(self.interaction)

    async def test_myid_is_private_and_requires_no_premium(self):
        self.bot.premium = PremiumConfig(enabled=False)
        await self.bot.tree.get_command("myid").callback(self.interaction)
        result = self.interaction.response.send_message.await_args
        self.assertTrue(result.kwargs["ephemeral"])
        self.assertIn(str(USER), result.args[0])
        self.assertIn(str(GUILD), result.args[0])
