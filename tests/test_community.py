import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from espresso.bot import Espresso
from espresso.community_config import channel_settings, default_raid, default_community, default_welcome
from espresso.rules import Settings, content_violation
from espresso.store import Store, SettingsConflict
from espresso.web import settings_json


def member(guild, user_id=10, staff=False):
    obj=Mock(spec=discord.Member)
    obj.id=user_id;obj.bot=False;obj.guild=guild;obj.roles=[];obj.top_role=5
    obj.guild_permissions=discord.Permissions(moderate_members=staff,manage_guild=staff)
    obj.send=AsyncMock();obj.timed_out_until=None
    return obj


def channel(guild, channel_id=20):
    obj=Mock(spec=discord.TextChannel)
    obj.id=channel_id;obj.guild=guild;obj.slowmode_delay=0
    obj.edit=AsyncMock();obj.set_permissions=AsyncMock();obj.send=AsyncMock(return_value=SimpleNamespace(id=500))
    obj.permissions_for=Mock(return_value=discord.Permissions.all())
    obj.overwrites_for=Mock(return_value=discord.PermissionOverwrite())
    return obj


class CommunityRulesTests(unittest.TestCase):
    def test_trusted_domains_check_hostname_and_subdomain_boundaries(self):
        settings=Settings(block_links=True,block_invites=False,allowed_domains=["example.com"])
        for text in ("https://example.com/a","www.example.com","https://images.example.com/photo"):
            self.assertIsNone(content_violation(text,settings))
        for text in ("https://example.com.evil.net","https://example.com@evil.net","https://evil.net/example.com","example.com https://bad.net"):
            self.assertEqual(content_violation(text,settings),"Link")
        settings.block_invites=True
        settings.allowed_domains.append("discord.gg")
        self.assertEqual(content_violation("discord.gg/hello",settings),"Discord invite")

    def test_per_channel_overrides_and_thread_inheritance(self):
        settings=Settings(enabled=True,block_links=True,channel_rules=[{"channel_id":20,"enabled":True,"block_links":False,"block_invites":None,"spam_enabled":None}])
        effective=channel_settings(settings,21,20)
        self.assertFalse(effective.block_links);self.assertTrue(effective.block_invites)
        self.assertTrue(channel_settings(settings,99).block_links)
        self.assertTrue(settings.block_links)
        settings.channel_rules[0]["enabled"]=False
        self.assertFalse(channel_settings(settings,20).enabled)

    def test_bad_configuration_is_rejected(self):
        bad=[{"observation_mode":1},{"warning_expiry_days":-1},{"allowed_domains":["https://example.com"]},
             {"channel_rules":[{"channel_id":None,"enabled":False,"block_links":None,"block_invites":None,"spam_enabled":None}]},
             {"raid":default_raid()|{"join_limit":True}}, {"raid":default_raid()|{"action":"ban"}},
             {"welcome":default_welcome()|{"enabled":True}},
             {"community":default_community()|{"prompts_enabled":True}}]
        for changes in bad:
            with self.subTest(changes=changes),self.assertRaises(ValueError):Settings(**changes)

    def test_nested_ids_are_serialized_without_javascript_precision_loss(self):
        huge=123456789012345678
        settings=Settings(welcome=default_welcome()|{"channel_id":huge},community=default_community()|{"notification_role_id":huge},channel_rules=[{"channel_id":huge,"enabled":True,"block_links":None,"block_invites":None,"spam_enabled":None}])
        result=settings_json(settings)
        self.assertEqual(result["welcome"]["channel_id"],str(huge))
        self.assertEqual(result["community"]["notification_role_id"],str(huge))
        self.assertEqual(result["channel_rules"][0]["channel_id"],str(huge))


class CommunityStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store=Store(":memory:");await self.store.open()

    async def asyncTearDown(self):
        await self.store.close()

    async def test_cases_warning_removal_expiry_and_clear_audit(self):
        now=time.time();await self.store.update(1,warning_expiry_days=1)
        first,_=await self.store.warn(1,10,99,"old","one",720,now=now-2*86400)
        second,count=await self.store.warn(1,10,99,"new","two",720,now=now)
        self.assertEqual(count,1)
        self.assertEqual(len(await self.store.warnings(1,10)),2)
        self.assertTrue(await self.store.void_warning(1,second,99,"Mistake"))
        self.assertFalse(await self.store.void_warning(1,second,99,"Retry"))
        _,count=await self.store.warn(1,10,99,"another","three",720,now=now+1)
        self.assertEqual(count,1)
        cases=await self.store.cases(1)
        self.assertIsNotNone(next(row for row in cases if row["warning_id"]==second)["voided_at"])
        await self.store.clear(1,10,99)
        self.assertTrue(all(row["voided_at"] for row in await self.store.cases(1)))
        self.assertEqual(await self.store.warnings(1,10),[])

    async def test_private_appeals_are_owned_and_server_scoped(self):
        case=await self.store.record_case(1,10,99,"timeout","reason","done","case1")
        for guild,user in ((2,10),(1,11)):
            with self.assertRaises(ValueError):await self.store.create_inbox(guild,"appeal",user,"please review","bad",case_id=case)
        item=await self.store.create_inbox(1,"appeal",10,"please review","good",case_id=case,now=1000)
        self.assertEqual(await self.store.create_inbox(1,"appeal",10,"please review","good",case_id=case,now=1001),item)
        with self.assertRaises(ValueError):await self.store.create_inbox(1,"appeal",10,"duplicate","again",case_id=case,now=1100)
        self.assertEqual(await self.store.inbox(2),[])
        with self.assertRaises(ValueError):await self.store.review_inbox(1,item,10,1,"claim")
        self.assertEqual(await self.store.review_inbox(1,item,99,1,"claim"),"in_review")
        with self.assertRaises(SettingsConflict):await self.store.review_inbox(1,item,98,1,"resolve","reviewed")
        with self.assertRaises(ValueError):await self.store.review_inbox(1,item,99,2,"resolve","")
        await self.store.review_inbox(1,item,99,2,"resolve","Warning removed separately")
        self.assertEqual(await self.store.inbox(1),[])
        self.assertEqual(len(await self.store.inbox_history(1,item)),2)
        await self.store.review_inbox(1,item,98,3,"reopen","New information")
        self.assertEqual((await self.store.inbox(1))[0]["assigned_id"],None)

    async def test_intake_throttling_and_open_item_limit(self):
        await self.store.create_inbox(1,"report",10,"concern","a",now=1000)
        with self.assertRaises(ValueError):await self.store.create_inbox(1,"report",10,"more","b",now=1010)
        for index in range(1,5):await self.store.create_inbox(1,"report",10,"concern",str(index),now=1000+index*61)
        with self.assertRaises(ValueError):await self.store.create_inbox(1,"report",10,"too many","limit",now=2000)

    async def test_schedule_claim_cancel_repeat_and_validation(self):
        for due in (float("nan"),float("inf"),999):
            with self.assertRaises(ValueError):await self.store.schedule_post(1,20,99,"hello",due,0,False,"invalid",now=1000)
        post=await self.store.schedule_post(1,20,99,"hello",1011,1,False,"one",now=1000)
        self.assertEqual(post,await self.store.schedule_post(1,20,99,"hello",1011,1,False,"one",now=1000))
        rows=await self.store.claim_due_posts(1011);self.assertEqual(len(rows),1)
        self.assertEqual(await self.store.claim_due_posts(1011),[])
        with self.assertRaises(ValueError):await self.store.cancel_post(1,post)
        await self.store.finish_post(rows[0],"sent","done",500,now=1011+3*86400)
        result=(await self.store.posts(1))[0]
        self.assertEqual(result["due_at"],1011+4*86400);self.assertEqual(result["status"],"pending")
        with self.assertRaises(ValueError):await self.store.cancel_post(2,post)
        await self.store.cancel_post(1,post)
        self.assertEqual(await self.store.claim_due_posts(1011+5*86400),[])
        # Retrying an acknowledged request remains idempotent after its original due date.
        self.assertEqual(await self.store.schedule_post(1,20,99,"hello",1011,1,False,"one",now=999999),post)
        with self.assertRaises(ValueError):await self.store.schedule_post(1,20,99,"different message",1011,1,False,"one",now=1000)

    async def test_restart_preserves_recovery_and_marks_ambiguous_sends(self):
        with TemporaryDirectory() as folder:
            store=Store(str(Path(folder)/"bot.db"));await store.open()
            await store.update(1,enabled=True,bad_words=["test"])
            await store.schedule_post(1,20,99,"hello",1011,0,False,"one",now=1000)
            await store.claim_due_posts(1011)
            await store.save_recovery(1,20,{"action":"slowmode","old":{"slowmode_delay":0},"applied":{"slowmode_delay":10}},1020)
            await store.close();await store.open()
            self.assertEqual((await store.posts(1))[0]["status"],"uncertain")
            self.assertEqual(len(await store.recovery()),1)
            self.assertTrue((await store.settings(1)).enabled)
            await store.delete_guild(1)
            self.assertEqual(await store.recovery(),[]);self.assertEqual(await store.posts(1),[])
            await store.close()


class CommunityManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot=Espresso(":memory:",member_screening_events=True)
        self.bot._connection.user=SimpleNamespace(id=99)
        await self.bot.store.open();self.features=self.bot.community_manager
        self.guild=SimpleNamespace(id=1,owner_id=100,name="Café",unavailable=False,default_role=SimpleNamespace(id=1))
        self.guild.me=member(self.guild,99,True);self.guild.me.top_role=10
        self.member=member(self.guild);self.actor=member(self.guild,90,True)
        self.channel=channel(self.guild)
        self.guild.fetch_channel=AsyncMock(return_value=self.channel)
        self.guild.fetch_channels=AsyncMock(return_value=[self.channel])
        self.guild.fetch_member=AsyncMock(return_value=self.actor)
        self.bot.get_guild=Mock(return_value=self.guild);self.bot.log_action=AsyncMock()

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_observation_mode_never_enforces(self):
        await self.bot.store.update(1,enabled=True,observation_mode=True,bad_words=["testword"])
        message=SimpleNamespace(guild=self.guild,author=self.member,content="testword",id=50,webhook_id=None,channel=self.channel,delete=AsyncMock(),is_system=lambda:False)
        await self.bot.moderate_message(message,edited=False)
        message.delete.assert_not_awaited();self.assertEqual(await self.bot.store.warnings(1,10),[])
        self.assertIn("observation",self.bot.log_action.await_args.args[1].lower())
        settings=Settings(observation_mode=True,raid=default_raid()|{"enabled":True,"action":"lockdown"})
        await self.features.raid_response(self.guild,settings,"Join spike")
        self.channel.set_permissions.assert_not_awaited();self.assertEqual(await self.bot.store.recovery(),[])

    async def test_raid_snapshot_precedes_write_and_restore_preserves_staff_changes(self):
        async def inspect_before_write(**kwargs):
            self.assertEqual((await self.bot.store.recovery())[0]["data"]["old"]["slowmode_delay"],0)
        self.channel.edit.side_effect=inspect_before_write
        await self.features.protect_channel(self.guild,20,"slowmode",default_raid())
        row=(await self.bot.store.recovery())[0]
        self.channel.slowmode_delay=15;self.channel.edit.reset_mock()
        await self.features.restore_channel(self.guild,row)
        self.assertEqual(self.channel.edit.await_args.kwargs["slowmode_delay"],0)
        self.channel.edit.side_effect=None
        await self.features.protect_channel(self.guild,20,"slowmode",default_raid())
        self.channel.slowmode_delay=60;self.channel.edit.reset_mock()
        await self.features.restore_channel(self.guild,(await self.bot.store.recovery())[0])
        self.channel.edit.assert_not_awaited()

    async def test_lockdown_restores_only_its_own_permission_bits(self):
        overwrite=discord.PermissionOverwrite(send_messages=None,add_reactions=True)
        self.channel.overwrites_for.return_value=overwrite
        await self.features.protect_channel(self.guild,20,"lockdown",default_raid())
        self.assertFalse(overwrite.send_messages)
        overwrite.create_private_threads=True # Staff changed this after protection.
        await self.features.restore_channel(self.guild,(await self.bot.store.recovery())[0])
        restored=self.channel.set_permissions.await_args.kwargs["overwrite"]
        self.assertIsNone(restored.send_messages);self.assertTrue(restored.add_reactions);self.assertTrue(restored.create_private_threads)

    async def test_failed_restore_retains_recovery_and_reports_once(self):
        await self.features.protect_channel(self.guild,20,"slowmode",default_raid())
        self.channel.slowmode_delay=15
        self.channel.edit.side_effect=discord.Forbidden(SimpleNamespace(status=403,reason="Forbidden"),"Forbidden")
        self.assertFalse(await self.features.restore_channel(self.guild,(await self.bot.store.recovery())[0]))
        self.assertFalse(await self.features.restore_channel(self.guild,(await self.bot.store.recovery())[0]))
        self.assertEqual(len(await self.bot.store.recovery()),1)
        self.bot.log_action.assert_awaited_once()

    async def test_repeat_detection_counts_distinct_members(self):
        settings=Settings(raid=default_raid()|{"enabled":True,"repeat_limit":3})
        self.features.raid_response=AsyncMock()
        message=SimpleNamespace(guild=self.guild,author=self.member,content="identical repeated message",channel=self.channel)
        for _ in range(5):await self.features.message(message,settings)
        self.features.raid_response.assert_not_awaited()
        for user_id in (11,12):message.author=member(self.guild,user_id);await self.features.message(message,settings)
        self.features.raid_response.assert_awaited_once()

    async def test_thread_raid_restricts_parent_instead_of_entire_server(self):
        settings=Settings(raid=default_raid()|{"enabled":True,"action":"slowmode"})
        thread=Mock(spec=discord.Thread);thread.parent_id=20
        self.features.protect_channel=AsyncMock()
        await self.features.raid_response(self.guild,settings,"Repeated text",thread)
        self.guild.fetch_channels.assert_not_awaited()
        self.features.protect_channel.assert_awaited_once_with(self.guild,20,"slowmode",settings.raid)

    async def test_join_spikes_and_welcome_cards(self):
        await self.bot.store.update(1,raid=default_raid()|{"enabled":True,"join_limit":3},welcome=default_welcome()|{"enabled":True,"channel_id":20})
        self.features.raid_response=AsyncMock()
        for user_id in (10,11,12):
            joined=member(self.guild,user_id);joined.mention=f"<@{user_id}>";joined.display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/embed/avatars/0.png")
            await self.features.joined(joined)
        self.features.raid_response.assert_awaited_once()
        self.assertEqual(self.channel.send.await_count,3)
        sent=self.channel.send.await_args.kwargs
        self.assertIn("<@12>",sent["embed"].description)
        self.assertFalse(sent["allowed_mentions"].users)
        bot_member=member(self.guild,13);bot_member.bot=True
        await self.features.joined(bot_member)
        self.assertEqual(self.channel.send.await_count,3)

    async def test_daily_prompts_queue_once_and_cancel_when_disabled(self):
        await self.bot.store.update(1,community=default_community()|{"prompts_enabled":True,"prompt_channel_id":20,"prompt_hour_utc":16})
        self.bot._connection._guilds={1:self.guild}
        today=datetime(2026,10,18,17,tzinfo=timezone.utc).timestamp()
        await self.features.tick(today)
        await self.features.tick(today+1)
        posts=await self.bot.store.posts(1)
        self.assertEqual(len(posts),1);self.assertTrue(posts[0]["source"].startswith("prompt:"))
        await self.bot.store.update(1,community=default_community())
        row=(await self.bot.store.claim_due_posts(today+20))[0]
        await self.features.deliver_post(row,today+20)
        self.channel.send.assert_not_awaited()
        self.assertEqual((await self.bot.store.posts(1))[0]["status"],"cancelled")

    async def test_schedule_rechecks_permissions_and_does_not_duplicate_uncertain_delivery(self):
        now=time.time()
        post=await self.features.schedule(self.guild,self.actor,20,"hello",now+11,0,False,"one")
        row=(await self.bot.store.claim_due_posts(now+12))[0]
        self.actor.guild_permissions.manage_guild=False
        await self.features.deliver_post(row,now+12)
        self.channel.send.assert_not_awaited();self.assertEqual((await self.bot.store.posts(1))[0]["status"],"failed")
        self.actor.guild_permissions.manage_guild=True
        await self.features.schedule(self.guild,self.actor,20,"again",now+13,0,False,"two")
        row=(await self.bot.store.claim_due_posts(now+14))[0]
        self.channel.send.side_effect=TimeoutError()
        await self.features.deliver_post(row,now+14)
        self.assertEqual((await self.bot.store.posts(1))[0]["status"],"uncertain")
        self.assertEqual(await self.bot.store.claim_due_posts(now+100),[])

    async def test_successful_post_blocks_unrequested_mentions(self):
        now=time.time();await self.features.schedule(self.guild,self.actor,20,"@everyone <@123>",now+11,0,False,"one")
        await self.features.deliver_post((await self.bot.store.claim_due_posts(now+12))[0],now+12)
        mentions=self.channel.send.await_args.kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone);self.assertFalse(mentions.users);self.assertFalse(mentions.roles)

    async def test_fun_commands_are_optional_and_respect_channel_permissions(self):
        interaction=SimpleNamespace(guild=self.guild,guild_id=1,user=self.member,channel=self.channel,response=SimpleNamespace(send_message=AsyncMock()))
        with self.assertRaises(ValueError):await self.features.coffee(interaction,self.actor)
        await self.bot.store.update(1,community=default_community()|{"coffee_enabled":True,"polls_enabled":True})
        self.features.require_public_post(interaction,poll=True)
        self.channel.permissions_for.return_value=discord.Permissions(view_channel=True)
        with self.assertRaises(ValueError):await self.features.coffee(interaction,self.actor)
        self.channel.permissions_for.return_value=discord.Permissions.all()
        with self.assertRaises(ValueError):await self.features.poll(interaction,"Question","same|SAME",24)
        await self.features.poll(interaction,"Question","Coffee|Tea",24)
        self.assertEqual(len(interaction.response.send_message.await_args.kwargs["poll"].answers),2)

    async def test_filter_test_never_records_a_case_or_warning(self):
        await self.bot.store.update(1,enabled=True,bad_words=["testword"])
        result=await self.features.test_filter(1,"testword")
        self.assertTrue(result["matched"]);self.assertEqual(result["action"],"warn")
        self.assertEqual(await self.bot.store.cases(1),[]);self.assertEqual(await self.bot.store.warnings(1,10),[])

    async def test_warning_removal_enforces_hierarchy_and_keeps_audit(self):
        warning_id,_=await self.bot.store.warn(1,10,99,"mistake","one",24)
        self.guild.fetch_member.return_value=self.member
        with self.assertRaises(ValueError):await self.features.remove_warning(self.guild,self.actor,warning_id,"Mistake")
        self.actor.top_role=9
        self.assertTrue(await self.features.remove_warning(self.guild,self.actor,warning_id,"Mistake"))
        self.assertEqual(await self.bot.store.warnings(1,10),[])
        self.assertEqual((await self.bot.store.cases(1))[0]["kind"],"warning_removed")

    async def test_private_thread_report_requires_membership(self):
        thread=Mock(spec=discord.Thread);thread.id=22;thread.guild=self.guild
        thread.permissions_for.return_value=discord.Permissions(view_channel=True,read_message_history=True)
        thread.is_private.return_value=True
        thread.fetch_member=AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404,reason="Not Found"),"Not Found"))
        thread.fetch_message=AsyncMock()
        self.guild.get_channel_or_thread=Mock(return_value=thread)
        interaction=SimpleNamespace(id=123,guild=self.guild,guild_id=1,user=self.actor,response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        with self.assertRaises(ValueError):
            await self.bot.tree.get_command("report").callback(interaction,self.member,"Concern","https://discord.com/channels/1/22/33")
        thread.fetch_message.assert_not_awaited()
        self.assertEqual(await self.bot.store.inbox(1),[])
