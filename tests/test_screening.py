from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace
import tempfile
from pathlib import Path
import time
import unittest
from unittest.mock import AsyncMock, Mock

import discord

from espresso.bot import Espresso
from espresso.rules import Settings
from espresso.screening import AgreementView, ScreeningEntryView
from espresso.screening_config import default_screening, rules_revision
from espresso.store import Store
from test_reaction_roles import Role


class ScreeningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Espresso(":memory:",member_screening_events=True)
        self.bot._connection.user = SimpleNamespace(id=99)
        await self.bot.store.open()
        self.bot.log_action = AsyncMock()
        self.role = Role(20,2)
        self.actor = SimpleNamespace(id=77,top_role=Role(77,8),guild_permissions=SimpleNamespace(manage_roles=True,manage_guild=True))
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 5
        self.channel.permissions_for.return_value = SimpleNamespace(view_channel=True,send_messages=True,embed_links=True)
        self.channel.send = AsyncMock(return_value=SimpleNamespace(jump_url="https://discord.com/channels/1/5/50",id=50))
        self.guild = SimpleNamespace(id=1,owner_id=100,me=SimpleNamespace(top_role=Role(99,10),guild_permissions=SimpleNamespace(manage_roles=True)),fetch_roles=AsyncMock(return_value=[self.role]),fetch_channels=AsyncMock(return_value=[self.channel]))
        self.member = SimpleNamespace(id=10,bot=False,roles=[],guild=self.guild,joined_at=discord.utils.utcnow(),created_at=discord.utils.utcnow()-timedelta(days=1),pending=False,guild_permissions=SimpleNamespace(administrator=False),add_roles=AsyncMock())
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        self.policy = {**default_screening(),"enabled":True,"verified_role_id":20,"entry_channel_id":5}
        await self.bot.store.update(1,screening=self.policy)
        self.manager = self.bot.screening_manager

    async def asyncTearDown(self):
        await self.bot.close()

    async def agree(self):
        return await self.manager.assess(self.guild,10,rules_revision(self.policy))

    async def test_everyone_is_held_until_agreement_and_staff_approval(self):
        await self.bot.on_member_join(self.member)
        case = await self.bot.store.screening_case(1,10)
        self.assertEqual(case["status"],"awaiting_agreement")
        await self.bot.on_member_join(self.member)
        self.assertEqual((await self.bot.store.screening_case(1,10))["version"],case["version"])
        self.member.add_roles.assert_not_awaited()
        with self.assertRaisesRegex(ValueError,"agree"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        await self.agree()
        case = await self.bot.store.screening_case(1,10)
        self.assertEqual(case["status"],"pending")
        result = await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        self.assertEqual(result["status"],"approved")
        self.assertEqual(result["reviewer_id"],77)
        self.member.add_roles.assert_awaited_once()
        self.assertEqual(await self.bot.store.screening_queue(1),[])
        with self.assertRaisesRegex(ValueError,"changed"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        self.member.add_roles.assert_awaited_once()

    async def test_older_accounts_auto_receive_access_only_after_agreement(self):
        self.policy["mode"] = "new_accounts"
        self.member.created_at -= timedelta(days=30)
        await self.bot.store.update(1,screening=self.policy)
        await self.manager.joined(self.member)
        self.member.add_roles.assert_not_awaited()
        await self.agree()
        self.member.add_roles.assert_awaited_once()
        case = await self.bot.store.screening_case(1,10)
        self.assertEqual(case["status"],"approved")
        self.assertIsNone(case["reviewer_id"])

    async def test_new_account_stays_held_and_denial_requires_staff_reopen(self):
        self.policy["mode"] = "new_accounts"
        await self.bot.store.update(1,screening=self.policy)
        await self.agree()
        case = await self.bot.store.screening_case(1,10)
        self.assertTrue(case["requires_review"])
        case = await self.manager.review(self.guild,self.actor,10,"deny",case["version"])
        self.assertIn("declined",await self.agree())
        with self.assertRaisesRegex(ValueError,"Reopen"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        case = await self.manager.review(self.guild,self.actor,10,"reopen",case["version"])
        await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        self.member.add_roles.assert_awaited_once()

    async def test_native_discord_screening_cannot_be_bypassed(self):
        self.member.pending = True
        with self.assertRaisesRegex(ValueError,"built-in"):
            await self.agree()
        self.member.add_roles.assert_not_awaited()
        self.member.pending = False
        await self.agree()
        self.member.pending = True
        case = await self.bot.store.screening_case(1,10)
        with self.assertRaisesRegex(ValueError,"built-in"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        self.member.add_roles.assert_not_awaited()

    async def test_changed_rules_require_fresh_agreement(self):
        old_hash = rules_revision(self.policy)
        await self.agree()
        self.policy["rules_text"] = "A new version of the server rules."
        await self.bot.store.update(1,screening=self.policy)
        with self.assertRaisesRegex(ValueError,"rules changed"):
            await self.manager.assess(self.guild,10,old_hash)
        case = await self.bot.store.screening_case(1,10)
        with self.assertRaisesRegex(ValueError,"current rules"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        await self.agree()
        case = await self.bot.store.screening_case(1,10)
        await self.manager.review(self.guild,self.actor,10,"approve",case["version"])

    async def test_permissions_hierarchy_self_review_and_discord_failure(self):
        await self.agree()
        case = await self.bot.store.screening_case(1,10)
        self.actor.guild_permissions.manage_roles = False
        with self.assertRaisesRegex(ValueError,"Manage Roles"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        self.actor.guild_permissions.manage_roles = True
        self.actor.id = 10
        with self.assertRaisesRegex(ValueError,"own"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        self.actor.id = 77
        self.role.permissions.administrator = True
        with self.assertRaisesRegex(ValueError,"staff"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        self.role.permissions.administrator = False
        self.role.position = 10
        with self.assertRaisesRegex(ValueError,"above"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        self.role.position = 2
        self.member.add_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403,reason="Forbidden"),"Forbidden")
        with self.assertRaises(discord.Forbidden):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        self.assertEqual((await self.bot.store.screening_case(1,10))["status"],"pending")

    async def test_rejoin_resets_agreement_and_invalidates_old_review(self):
        await self.agree()
        case = await self.bot.store.screening_case(1,10)
        self.member.joined_at += timedelta(seconds=30)
        with self.assertRaisesRegex(ValueError,"rejoined"):
            await self.manager.review(self.guild,self.actor,10,"approve",case["version"])
        await self.manager.joined(self.member)
        new = await self.bot.store.screening_case(1,10)
        self.assertIsNone(new["agreed_at"])
        self.assertGreater(new["version"],case["version"])

    async def test_departed_members_leave_queue_without_closing_a_quick_rejoin(self):
        await self.agree()
        self.bot.get_guild = Mock(return_value=self.guild)
        await self.manager.left(1,10)
        self.assertEqual((await self.bot.store.screening_case(1,10))["status"],"pending")
        self.guild.fetch_member.side_effect = discord.NotFound(SimpleNamespace(status=404,reason="Not Found"),"Not Found")
        await self.bot.on_raw_member_remove(SimpleNamespace(guild_id=1,user=SimpleNamespace(id=10)))
        self.assertEqual(await self.bot.store.screening_queue(1),[])
        self.assertEqual((await self.bot.store.screening_case(1,10))["status"],"departed")

    async def test_entry_message_is_explicit_and_agreement_is_bound_to_member(self):
        url = await self.manager.publish(self.guild,self.actor)
        self.assertIn("/channels/",url)
        view = self.channel.send.await_args.kwargs["view"]
        self.assertIsInstance(view,ScreeningEntryView)
        self.assertTrue(view.is_persistent())
        agreement = AgreementView(self.manager,1,10,rules_revision(self.policy))
        interaction = SimpleNamespace(guild_id=1,user=SimpleNamespace(id=11),response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await agreement.children[0].callback(interaction)
        self.assertIn("own verification",interaction.followup.send.await_args.args[0])
        self.member.add_roles.assert_not_awaited()

    async def test_website_activity_is_recorded_with_discord_channel_logging_off(self):
        result = await Espresso.log_action(self.bot,self.guild,"Automatic filter","Member 10 · rule Message spam · Message deleted.")
        self.assertFalse(result)
        rows = await self.bot.store.activity_logs(1)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["kind"],"moderation")
        self.assertNotIn("message body",rows[0]["details"])

    async def test_intent_and_config_validation_and_reaction_bypass(self):
        self.bot._connection._intents.members = False
        with self.assertRaisesRegex(ValueError,"member events"):
            await self.manager.validate(self.guild,self.actor,self.policy)
        for invalid in ({"minimum_account_age_days":0},{"minimum_account_age_days":True},{"enabled":"true"},{"verified_role_id":None},{"rules_text":" "}):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                Settings(screening={**self.policy,**invalid})
        mapping = {"channel_id":5,"message_id":50,"role_id":20,"emoji":"☕","remove_on_unreact":True}
        with self.assertRaisesRegex(ValueError,"reaction role"):
            Settings(screening=self.policy,reaction_roles=[mapping])
        mapping["role_id"] = 21
        role = Role(21,1)
        self.guild.fetch_roles.return_value = [self.role,role]
        self.bot.get_guild = Mock(return_value=self.guild)
        await self.bot.store.update(1,reaction_roles=[mapping],reaction_roles_enabled=True)
        payload = SimpleNamespace(guild_id=1,user_id=10,channel_id=5,message_id=50,emoji="☕")
        await self.bot.reaction_manager.handle(payload,added=True)
        self.member.add_roles.assert_not_awaited()
        self.member.roles = [self.role]
        await self.bot.reaction_manager.handle(payload,added=True)
        self.member.add_roles.assert_awaited_once()

    async def test_cases_and_audit_survive_restart_and_server_deletion_cleans_up(self):
        await self.agree()
        case = await self.bot.store.screening_case(1,10)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/"screening.db")
            store = Store(path)
            await store.open()
            case["version"] = 0
            await store.save_screening_case(case,"agreed",10)
            await store.log_activity(1,"Verification review","Member 10 · pending")
            await store.close()
            await store.open()
            self.assertEqual((await store.screening_case(1,10))["status"],"pending")
            self.assertEqual(len(await store.activity_logs(1)),1)
            await store.delete_guild(1)
            self.assertIsNone(await store.screening_case(1,10))
            self.assertEqual(await store.activity_logs(1),[])
            async with store.db.execute("SELECT COUNT(*) FROM screening_audit") as cursor:
                self.assertEqual((await cursor.fetchone())[0],0)
            await store.close()


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_isolation_filter_pagination_and_retention(self):
        store = Store(":memory:")
        await store.open()
        try:
            for i in range(55):
                await store.log_activity(1,"Automatic filter",f"Member {i}")
            await store.log_activity(2,"Settings changed","Other server")
            first = await store.activity_logs(1)
            self.assertEqual(len(first),51)
            older = await store.activity_logs(1,first[49]["id"])
            self.assertEqual(len(older),5)
            self.assertFalse({r["id"] for r in first[:50]} & {r["id"] for r in older})
            self.assertEqual(await store.activity_logs(1,kind="settings"),[])
            await store.db.execute("INSERT INTO activity_logs(guild_id,kind,title,details,created_at) VALUES (1,'system','old','old',?)",(time.time()-31*86400,))
            await store.db.commit()
            await store.log_activity(1,"Settings changed","New")
            async with store.db.execute("SELECT COUNT(*) FROM activity_logs WHERE title='old'") as cursor:
                self.assertEqual((await cursor.fetchone())[0],0)
            await store.db.executemany("INSERT INTO activity_logs(guild_id,kind,title,details,created_at) VALUES (3,'system','entry','metadata',?)",[(time.time(),)]*10001)
            await store.db.commit()
            await store.log_activity(3,"New entry","bounded")
            async with store.db.execute("SELECT COUNT(*) FROM activity_logs WHERE guild_id=3") as cursor:
                self.assertEqual((await cursor.fetchone())[0],10000)
        finally:
            await store.close()
