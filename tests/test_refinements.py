from dataclasses import asdict
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock

import discord

import test_community as community_fixture
import test_community_web as web_fixture
from espresso.rules import Settings
from espresso.community_config import default_raid, default_community
from espresso.scheduling import calendar_policy, local_timestamp, next_occurrence
from espresso.simulation import simulate
from espresso.role_menus import validate_menu, RoleMenuView
from espresso.store import SettingsConflict


class CalendarTests(unittest.TestCase):
    def test_named_zone_weekly_stays_at_local_time(self):
        policy=calendar_policy({"timezone":"America/New_York","recurrence":"weekly","weekdays":[4],"local_time":"18:00"})
        before=local_timestamp("2026-03-06T18:00","America/New_York")
        following=next_occurrence(before,policy)
        self.assertEqual(following,local_timestamp("2026-03-13T18:00","America/New_York"))
        self.assertEqual(following-before,7*86400-3600)

    def test_nonexistent_wall_time_rejected_and_daily_occurrence_skipped(self):
        with self.assertRaises(ValueError):local_timestamp("2026-03-08T02:30","America/New_York")
        policy=calendar_policy({"timezone":"America/New_York","recurrence":"daily","local_time":"02:30"})
        before=local_timestamp("2026-03-07T02:30","America/New_York")
        self.assertEqual(next_occurrence(before,policy),local_timestamp("2026-03-09T02:30","America/New_York"))

    def test_ambiguous_wall_time_occurs_once(self):
        first=local_timestamp("2026-11-01T01:30","America/New_York")
        policy=calendar_policy({"timezone":"America/New_York","recurrence":"daily","local_time":"01:30"})
        self.assertEqual(next_occurrence(first,policy),local_timestamp("2026-11-02T01:30","America/New_York"))

    def test_invalid_zone_and_repeat_values(self):
        for policy in ({"timezone":"No/SuchZone"},{"recurrence":"weekly"},{"local_time":"25:00"},{"weekdays":[True]}):
            with self.subTest(policy=policy),self.assertRaises(ValueError):calendar_policy(policy)


class SimulationTests(unittest.TestCase):
    def test_exemption_cooldown_escalation_and_observation(self):
        settings=Settings(enabled=True,bad_words=["testword"],exempt_roles=[20],warn_threshold=3)
        exempt=simulate("testword",settings,{"role_ids":[20],"active_warnings":2})
        self.assertEqual(exempt["effective_action"],"none");self.assertIn("Exempt",exempt["note"])
        cooldown=simulate("testword",settings,{"active_warnings":2,"warning_cooldown":True})
        self.assertEqual(cooldown["effective_action"],"delete");self.assertEqual(cooldown["warnings_after"],2)
        timeout=simulate("testword",settings,{"active_warnings":2})
        self.assertTrue(timeout["timeout_would_apply"])
        blocked=simulate("testword",settings,{"active_warnings":2},timeout_error="Role hierarchy prevents this timeout.")
        self.assertFalse(blocked["timeout_would_apply"]);self.assertEqual(blocked["effective_action"],"warn")
        settings.observation_mode=True
        self.assertEqual(simulate("testword",settings,{"active_warnings":2})["effective_action"],"observe")

    def test_spam_counts_current_message_and_ignores_edits(self):
        settings=Settings(enabled=True,spam_limit=3)
        self.assertEqual(simulate("hello",settings,{"recent_messages":2})["rule"],"Message spam")
        self.assertFalse(simulate("hello",settings,{"recent_messages":2,"edited":True})["matched"])

    def test_context_limits_are_validated(self):
        for value in ({"active_warnings":True},{"recent_messages":51},{"role_ids":["20"]},{"edited":1}):
            with self.assertRaises(ValueError):simulate("hello",Settings(),value)


class RefinementStoreTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=community_fixture.CommunityStoreTests.asyncSetUp
    asyncTearDown=community_fixture.CommunityStoreTests.asyncTearDown

    async def test_history_records_actor_full_snapshot_and_preserves_new_versions(self):
        await self.store.update(1,actor_id=10,enabled=True)
        await self.store.update(1,actor_id=11,block_links=True)
        history=await self.store.settings_history(1)
        self.assertEqual([row["version"] for row in history],[2,1,0])
        self.assertEqual(history[0]["actor_id"],11);self.assertIn("block_links",history[0]["changed_keys"])
        prior=await self.store.historical_settings(1,1)
        self.assertTrue(prior.enabled);self.assertFalse(prior.block_links)
        with self.assertRaises(ValueError):await self.store.historical_settings(2,1)
        with self.assertRaises(SettingsConflict):await self.store.update_from_web(1,1,10,{"block_links":False})

    async def test_member_report_view_never_contains_internal_notes(self):
        item=await self.store.create_inbox(1,"report",12,"Original report","report")
        await self.store.review_inbox(1,item,10,1,"resolve","INTERNAL SECRET","We reviewed your concern.",False)
        rows=await self.store.member_reports(1,12)
        self.assertEqual(rows[0]["public_response"],"We reviewed your concern.")
        self.assertNotIn("INTERNAL SECRET",json.dumps(rows));self.assertNotIn("Original report",json.dumps(rows))
        self.assertEqual(await self.store.member_reports(1,13),[])
        self.assertEqual(await self.store.member_reports(2,12),[])

    async def test_post_pause_edit_resume_and_stale_action_are_serialized(self):
        now=time.time();post_id=await self.store.schedule_post(1,20,10,"hello",now+100,0,False,"post")
        self.assertEqual(await self.store.change_post(1,post_id,1,"pause",10),"paused")
        self.assertEqual(await self.store.claim_due_posts(now+200),[])
        with self.assertRaises(SettingsConflict):await self.store.change_post(1,post_id,1,"resume",10)
        self.assertEqual(await self.store.change_post(1,post_id,2,"edit",11,{"content":"edited","due_at":now+300}),"paused")
        self.assertEqual(await self.store.change_post(1,post_id,3,"resume",11),"pending")
        claimed=(await self.store.claim_due_posts(now+301))[0]
        with self.assertRaises(ValueError):await self.store.change_post(1,post_id,5,"edit",11,{"content":"too late"})
        await self.store.finish_post(claimed,"uncertain","Unknown delivery")
        row=await self.store.post(1,post_id)
        with self.assertRaises(ValueError):await self.store.change_post(1,post_id,row["version"],"retry",11,{"due_at":now+500})
        await self.store.change_post(1,post_id,row["version"],"retry",11,{"due_at":now+500},True)
        self.assertEqual((await self.store.post(1,post_id))["actor_id"],11)

    async def test_overview_is_scoped_by_permission(self):
        await self.store.create_inbox(1,"report",12,"private body","one",now=time.time()-3*86400)
        staff=await self.store.overview(1,moderator=True)
        self.assertEqual(staff["reports"]["overdue"],1)
        self.assertNotIn("post_issues",staff);self.assertNotIn("private body",json.dumps(staff))
        self.assertNotIn("reports",await self.store.overview(1,manager=True))

    async def test_guild_removal_clears_history_menus_and_grants(self):
        await self.store.update(1,enabled=True)
        menu,_=await self.store.begin_menu(1,10,20,{"example":True},"source")
        await self.store.save_menu_grants(1,menu,12,{30})
        await self.store.delete_guild(1)
        self.assertEqual(await self.store.settings_history(1),[]);self.assertEqual(await self.store.menus(1),[])
        self.assertEqual(await self.store.menu_grants(1,menu,12),set())


class RefinementManagerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=community_fixture.CommunityManagerTests.asyncSetUp
    asyncTearDown=community_fixture.CommunityManagerTests.asyncTearDown

    async def test_closed_dm_does_not_lose_resolution_or_expose_staff_notes(self):
        item=await self.bot.store.create_inbox(1,"report",12,"concern","one")
        recipient=SimpleNamespace(send=AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403,reason="Forbidden"),"Forbidden")))
        self.bot.fetch_user=AsyncMock(return_value=recipient)
        await self.features.review(self.guild,self.actor,item,1,"resolve","PRIVATE STAFF NOTE","Public explanation",True)
        self.assertNotIn("PRIVATE STAFF NOTE",recipient.send.await_args.args[0])
        self.assertIn("Public explanation",recipient.send.await_args.args[0])
        row=await self.bot.store.inbox_item(1,item)
        self.assertEqual(row["notice_status"],"closed_dm");self.assertEqual(row["status"],"resolved")

    async def test_raid_exclusions_and_extension_keep_original_snapshot(self):
        self.channel.category_id=40
        with self.assertRaises(ValueError):await self.features.protect_channel(self.guild,20,"slowmode",default_raid()|{"excluded_category_ids":[40]})
        self.channel.edit.assert_not_awaited()
        await self.features.protect_channel(self.guild,20,"slowmode",default_raid())
        before=(await self.bot.store.recovery(1))[0]
        await self.features.extend_protection(self.guild,self.actor,20,10)
        after=(await self.bot.store.recovery(1))[0]
        self.assertEqual(before["data"]["old"],after["data"]["old"]);self.assertGreater(after["restore_at"],before["restore_at"])

    async def test_custom_card_keeps_mentions_suppressed(self):
        self.member.mention="<@10>";self.actor.mention="<@90>";self.actor.display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/embed/avatars/0.png")
        await self.bot.store.update(1,community=default_community()|{"coffee_enabled":True,"coffee_style":"custom","coffee_color":"123456","coffee_title":"Our café","coffee_message":"{sender} sends {member} {drink}."})
        interaction=SimpleNamespace(guild=self.guild,guild_id=1,channel=self.channel,user=self.member,response=SimpleNamespace(send_message=AsyncMock()))
        await self.features.coffee(interaction,self.actor)
        sent=interaction.response.send_message.await_args.kwargs
        self.assertEqual(sent["embed"].color.value,0x123456);self.assertEqual(sent["embed"].title,"Our café")
        self.assertIn("<@90>",sent["embed"].description);self.assertFalse(sent["allowed_mentions"].users)

    async def test_native_member_report_status_is_private_and_omits_internal_notes(self):
        item=await self.bot.store.create_inbox(1,"appeal",10,"concern","report",case_id=await self.bot.store.record_case(1,10,99,"warning","Rule","Recorded","warning-one"))
        await self.bot.store.review_inbox(1,item,90,1,"resolve","SECRET STAFF NOTE","We reviewed your appeal.")
        interaction=SimpleNamespace(guild_id=1,user=self.member,response=SimpleNamespace(send_message=AsyncMock()))
        await self.bot.tree.get_command("myreports").callback(interaction)
        sent=interaction.response.send_message.await_args
        self.assertTrue(sent.kwargs["ephemeral"]);self.assertIn("We reviewed",sent.args[0]);self.assertNotIn("SECRET STAFF",sent.args[0])

    async def test_native_schedule_uses_named_zone_and_weekday(self):
        from zoneinfo import ZoneInfo
        local=datetime.fromtimestamp(time.time()+2*86400,ZoneInfo("America/Phoenix")).strftime("%Y-%m-%dT18:00")
        interaction=SimpleNamespace(id=700,guild=self.guild,guild_id=1,user=self.actor,response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await self.bot.tree.get_command("community").get_command("schedule").callback(interaction,self.channel,local,"Friday café",0,False,"America/Phoenix","weekly","4")
        row=(await self.bot.store.posts(1))[0]
        due=datetime.fromtimestamp(row["due_at"],ZoneInfo("America/Phoenix"))
        self.assertEqual((due.weekday(),due.hour),(4,18));self.assertEqual(row["recurrence"],"weekly")

    async def test_native_simulator_explains_member_role_exemption(self):
        await self.bot.store.update(1,enabled=True,bad_words=["testword"],exempt_roles=[31])
        self.member.roles=[SimpleNamespace(id=31)];self.guild.fetch_member.return_value=self.member
        interaction=SimpleNamespace(guild=self.guild,guild_id=1,channel=self.channel,channel_id=20,user=self.actor,response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await self.bot.tree.get_command("community").get_command("testfilter").callback(interaction,"testword",None,self.member,2,0,False,False)
        self.assertIn("Exempt",interaction.followup.send.await_args.args[0]);self.assertEqual(await self.bot.store.cases(1),[])


class RefinementWebTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=web_fixture.CommunityWebTests.asyncSetUp
    asyncTearDown=web_fixture.CommunityWebTests.asyncTearDown
    post=web_fixture.CommunityWebTests.post

    async def test_moderators_get_staff_dashboard_without_configuration(self):
        self.panel.api.guilds.return_value=[{"id":"1","name":"Café","permissions":str(1<<40)}]
        self.actor.guild_permissions.manage_guild=False
        await self.bot.store.update(1,bad_words=["PRIVATE FILTER CONFIG"])
        servers=await (await self.client.get("/api/servers?include_settings=1")).json()
        self.assertEqual(len(servers["servers"]),1);self.assertFalse(servers["initial_server"]["data"]["access"]["manage"])
        response=await self.client.get("/api/servers/1/settings");self.assertEqual(response.status,200)
        self.assertNotIn("PRIVATE FILTER CONFIG",await response.text())
        for path in ("inbox","cases","overview"):
            self.assertEqual((await self.client.get("/api/servers/1/"+path)).status,200,path)
        for path in ("posts","calendar","health","settings-history","role-menus"):
            self.assertEqual((await self.client.get("/api/servers/1/"+path)).status,403,path)
        response=await self.client.patch("/api/servers/1/settings",json={"version":1,"changes":{"enabled":True}},headers=self.headers)
        self.assertEqual(response.status,403)
        self.actor.guild_permissions.moderate_members=False
        self.assertEqual((await self.client.get("/api/servers/1/inbox")).status,403)

    async def test_unsaved_filter_simulation_does_not_save_settings(self):
        response=await self.post("test-filter",{"text":"hypothetical","settings":{"enabled":True,"bad_words":["hypothetical"]},"context":{"active_warnings":2,"role_ids":[]}})
        self.assertEqual(response.status,200,await response.text());self.assertTrue((await response.json())["timeout_would_apply"])
        self.assertEqual((await self.bot.store.settings(1)).bad_words,[])
        self.assertEqual(await self.bot.store.cases(1),[])

    async def test_history_preview_is_read_only_and_restore_is_normal_versioned_save(self):
        await self.bot.store.update(1,actor_id=10,enabled=True)
        await self.bot.store.update(1,actor_id=11,block_links=True)
        response=await self.client.get("/api/servers/1/settings-history/1");self.assertEqual(response.status,200)
        preview=await response.json();self.assertEqual(preview["changes"],{"block_links":False})
        self.assertEqual((await self.bot.store.snapshot(1))[1],2)
        restored=await self.client.patch("/api/servers/1/settings",json={"version":preview["version"],"changes":preview["changes"]},headers=self.headers)
        self.assertEqual(restored.status,200);self.assertEqual((await restored.json())["version"],3)

    async def test_weekly_schedule_calendar_and_versioned_edit(self):
        tomorrow=datetime.fromtimestamp(time.time()+86400,timezone.utc).strftime("%Y-%m-%dT18:00")
        body={"channel_id":"20","content":"Friday café","due_at":time.time()+86400,"repeat_days":0,"notify":False,"request_id":"weekly-post-123","local_start":tomorrow,"calendar":{"timezone":"America/Phoenix","recurrence":"weekly","weekdays":[4],"local_time":"18:00"}}
        response=await self.post("posts",body);self.assertEqual(response.status,201,await response.text());post_id=(await response.json())["id"]
        calendar=await (await self.client.get(f"/api/servers/1/calendar?start={time.time()}&end={time.time()+30*86400}")).json()
        self.assertGreaterEqual(len(calendar["events"]),3)
        response=await self.post(f"posts/{post_id}/action",{"version":1,"action":"pause"});self.assertEqual(response.status,200)
        response=await self.post(f"posts/{post_id}/action",{"version":1,"action":"pause"});self.assertEqual(response.status,409)
        response=await self.post(f"posts/{post_id}/action",{"version":2,"action":"edit","changes":{"content":"Changed café"}});self.assertEqual(response.status,200,await response.text())
        response=await self.post(f"posts/{post_id}/action",{"version":3,"action":"resume"});self.assertEqual(response.status,200,await response.text())


class TestRole:
    def __init__(self,role_id,position=1):
        self.id=role_id;self.position=position;self.permissions=discord.Permissions.none();self.managed=False
    def is_default(self):return False
    def __ge__(self,other):return self.position>=other


class RoleMenuTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown=community_fixture.CommunityManagerTests.asyncTearDown

    async def asyncSetUp(self):
        await community_fixture.CommunityManagerTests.asyncSetUp(self)
        self.roles=[TestRole(31),TestRole(32,2)];self.guild.fetch_roles=AsyncMock(return_value=self.roles)
        self.actor.guild_permissions.manage_roles=True;self.actor.top_role=10;self.guild.me.top_role=20
        self.guild.me.guild_permissions.manage_roles=True
        self.manager=self.bot.role_menu_manager
        self.data={"title":"Our roles","description":"Find your people","style":"buttons","min_values":0,"max_values":1,"options":[{"role_id":role.id,"label":f"Role {role.id}","description":"A community role"} for role in self.roles]}
        self.member.roles=[];self.member.pending=False;self.member.add_roles=AsyncMock(side_effect=self.add);self.member.remove_roles=AsyncMock(side_effect=self.remove)
        self.guild.fetch_member.return_value=self.member
        self.interaction=SimpleNamespace(guild=self.guild,guild_id=1,user=self.member,message=SimpleNamespace(id=500),response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))

    async def add(self,*roles,**kwargs):self.member.roles.extend(role for role in roles if role not in self.member.roles)
    async def remove(self,*roles,**kwargs):self.member.roles=[role for role in self.member.roles if role not in roles]

    async def test_persistent_buttons_choose_one_and_track_only_owned_roles(self):
        row=await self.manager.publish(self.guild,self.actor,20,self.data,"one")
        self.assertTrue(RoleMenuView(self.manager,row).is_persistent())
        await self.manager.handle(self.interaction,row["id"],toggle=31)
        self.assertEqual({role.id for role in self.member.roles},{31})
        await self.manager.handle(self.interaction,row["id"],toggle=32)
        self.assertEqual({role.id for role in self.member.roles},{32})
        self.assertEqual(await self.bot.store.menu_grants(1,row["id"],10),{32})
        await self.manager.restore_views()

    async def test_manual_role_is_not_removed_and_changed_privileges_are_rechecked(self):
        row=await self.manager.publish(self.guild,self.actor,20,self.data,"one")
        self.member.roles=[self.roles[0]]
        await self.manager.handle(self.interaction,row["id"],toggle=32)
        self.member.remove_roles.assert_not_awaited();self.member.add_roles.assert_not_awaited()
        self.member.roles=[];self.roles[0].permissions.manage_roles=True
        await self.manager.handle(self.interaction,row["id"],toggle=31)
        self.member.add_roles.assert_not_awaited()

    async def test_publish_is_idempotent_disable_rejects_stale_buttons(self):
        row=await self.manager.publish(self.guild,self.actor,20,self.data,"one")
        again=await self.manager.publish(self.guild,self.actor,20,self.data,"one")
        self.assertEqual(row["id"],again["id"]);self.channel.send.assert_awaited_once()
        self.channel.fetch_message=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
        await self.manager.disable(self.guild,self.actor,row["id"])
        await self.manager.handle(self.interaction,row["id"],toggle=31)
        self.member.add_roles.assert_not_awaited()

    async def test_dropdown_options_and_limits_are_checked(self):
        row=await self.manager.publish(self.guild,self.actor,20,self.data|{"style":"select"},"one")
        view=RoleMenuView(self.manager,row)
        self.assertEqual(len(view.children[0].options),2)
        await self.manager.handle(self.interaction,row["id"],selected=[31,32])
        self.member.add_roles.assert_not_awaited()
        with self.assertRaises(ValueError):validate_menu(self.data|{"max_values":3})

    async def test_verification_gate_blocks_menu_grants(self):
        from espresso.screening_config import default_screening
        row=await self.manager.publish(self.guild,self.actor,20,self.data,"one")
        await self.bot.store.update(1,screening=default_screening()|{"enabled":True,"verified_role_id":33,"entry_channel_id":20})
        await self.manager.handle(self.interaction,row["id"],toggle=31)
        self.member.add_roles.assert_not_awaited();self.assertIn("/verify",self.interaction.followup.send.await_args.args[0])

    async def test_partially_confirmed_role_add_can_be_reconciled_and_removed(self):
        row=await self.manager.publish(self.guild,self.actor,20,self.data,"one")
        async def partial(*roles,**kwargs):
            await self.add(*roles)
            raise discord.HTTPException(SimpleNamespace(status=500,reason="Server error"),"Server error")
        self.member.add_roles.side_effect=partial
        await self.manager.handle(self.interaction,row["id"],toggle=31)
        self.assertEqual(await self.bot.store.menu_grants(1,row["id"],10),{31})
        await self.manager.handle(self.interaction,row["id"],toggle=31)
        self.assertEqual(self.member.roles,[]);self.assertEqual(await self.bot.store.menu_grants(1,row["id"],10),set())
