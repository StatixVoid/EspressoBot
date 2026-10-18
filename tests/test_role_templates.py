import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, create_autospec

import discord

from espresso.reaction_manager import ReactionRoleManager
from espresso.role_templates import CATALOG, TemplatePublisher, validate_request
from espresso.store import SettingsConflict, Store
from tests.test_reaction_roles import Role


def draft(**changes):
    return {"request_id":"a"*32,"version":0,"template_id":"pronouns","channel_id":"5",
            "title":"Your pronouns","description":"Choose any or none.","remove_on_unreact":True,
            "options":[{"name":"She / her","emoji":"🌷","role_id":None},{"name":"They / them","emoji":"⭐","role_id":None}],**changes}


class TemplateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store=Store(":memory:");await self.store.open()
        self.actor=SimpleNamespace(id=77,top_role=Role(77,8),guild_permissions=SimpleNamespace(manage_roles=True))
        self.me=SimpleNamespace(top_role=Role(99,10),guild_permissions=SimpleNamespace(manage_roles=True))
        self.roles=[Role(1),Role(20)];self.roles[1].name="Existing community role"
        self.roles[1].delete=AsyncMock()
        self.channel=Mock(spec=discord.TextChannel);self.channel.id=5
        self.channel.permissions_for.return_value=SimpleNamespace(view_channel=True,send_messages=True,embed_links=True,read_message_history=True,add_reactions=True)
        async def delete_message(*, delay=None):
            pass
        self.message=SimpleNamespace(id=50,add_reaction=AsyncMock(),delete=create_autospec(delete_message))
        self.channel.send=AsyncMock(return_value=self.message)
        async def create(**kwargs):
            role=Role(100+len(self.roles));role.name=kwargs["name"];role.delete=AsyncMock();self.roles.append(role);return role
        self.guild=SimpleNamespace(id=1,owner_id=1000,me=self.me,emojis=[],fetch_member=AsyncMock(return_value=self.actor),
                                   fetch_roles=AsyncMock(side_effect=lambda:list(self.roles)),fetch_channels=AsyncMock(return_value=[self.channel]),create_role=AsyncMock(side_effect=create))
        self.bot=SimpleNamespace(log_action=AsyncMock());self.bot.reaction_manager=ReactionRoleManager(self.bot)
        self.publisher=TemplatePublisher(self.store,self.bot)

    async def asyncTearDown(self):
        await self.store.close()

    async def test_posts_embed_creates_safe_roles_and_enables_saved_mappings(self):
        result=await self.publisher.publish(self.guild,self.actor,draft())
        self.assertEqual(result["state"],"complete");self.assertFalse(result["warnings"])
        for call in self.guild.create_role.await_args_list:
            self.assertEqual(call.kwargs["permissions"].value,0)
            self.assertFalse(call.kwargs["hoist"]);self.assertFalse(call.kwargs["mentionable"])
        send=self.channel.send.await_args.kwargs
        self.assertEqual(send["embed"].title,"Your pronouns")
        self.assertEqual(send["allowed_mentions"].to_dict(),discord.AllowedMentions.none().to_dict())
        settings,version=await self.store.snapshot(1)
        self.assertEqual(version,1);self.assertTrue(settings.reaction_roles_enabled)
        self.assertEqual(len(settings.reaction_roles),2);self.assertEqual(settings.reaction_roles[0]["message_id"],50)
        self.assertEqual(self.message.add_reaction.await_count,2)
        operation=await self.store.template_operation(1,"a"*32)
        self.assertEqual(operation["state"],"complete")

    async def test_retries_and_concurrent_clicks_return_same_message(self):
        first,second=await asyncio.gather(*[self.publisher.publish(self.guild,self.actor,draft()) for _ in range(2)])
        self.assertEqual(first,second);self.channel.send.assert_awaited_once()
        self.assertEqual(self.guild.create_role.await_count,2)
        self.publisher=TemplatePublisher(self.store,self.bot)  # same journal after replacing service
        again=await self.publisher.publish(self.guild,self.actor,draft())
        self.assertEqual(again["message_id"],"50");self.channel.send.assert_awaited_once()
        with self.assertRaisesRegex(ValueError,"different choices"):
            await self.publisher.publish(self.guild,self.actor,draft(title="Different"))

    async def test_existing_role_is_only_used_when_explicitly_selected(self):
        body=draft(options=[{"name":"My label","emoji":"✅","role_id":"20"}])
        result=await self.publisher.publish(self.guild,self.actor,body)
        self.assertEqual(result["state"],"complete");self.guild.create_role.assert_not_awaited()
        self.assertEqual((await self.store.settings(1)).reaction_roles[0]["role_id"],20)

    async def test_duplicate_name_requires_explicit_existing_role_selection(self):
        body=draft(options=[{"name":"Existing community role","emoji":"✅","role_id":None}])
        with self.assertRaisesRegex(ValueError,"already exists"):
            await self.publisher.publish(self.guild,self.actor,body)
        self.channel.send.assert_not_awaited();self.guild.create_role.assert_not_awaited()

    async def test_protected_roles_wrong_guild_and_missing_permissions_rejected_before_writes(self):
        body=draft(options=[{"name":"Label","emoji":"✅","role_id":"20"}])
        for change in ("administrator","manage_roles","mention_everyone"):
            setattr(self.roles[1].permissions,change,True)
            with self.assertRaises(ValueError):await self.publisher.publish(self.guild,self.actor,body)
            setattr(self.roles[1].permissions,change,False)
        self.roles[1].position=8
        with self.assertRaisesRegex(ValueError,"Your highest role"):await self.publisher.publish(self.guild,self.actor,body)
        self.roles[1].position=1
        with self.assertRaises(ValueError):await self.publisher.publish(self.guild,self.actor,draft(channel_id="999"))
        self.channel.permissions_for.return_value.embed_links=False
        with self.assertRaisesRegex(ValueError,"Embed Links"):await self.publisher.publish(self.guild,self.actor,draft())
        self.channel.permissions_for.return_value.embed_links=True;self.actor.guild_permissions.manage_roles=False
        with self.assertRaisesRegex(ValueError,"Manage Roles"):await self.publisher.publish(self.guild,self.actor,draft())
        self.channel.send.assert_not_awaited();self.guild.create_role.assert_not_awaited()

    async def test_screening_role_and_used_roles_cannot_be_mapped(self):
        settings=await self.store.settings(1)
        await self.store.update(1,screening={**settings.screening,"verified_role_id":20})
        body=draft(version=1,options=[{"name":"Verified","emoji":"✅","role_id":"20"}])
        with self.assertRaisesRegex(ValueError,"reserved"):await self.publisher.publish(self.guild,self.actor,body)
        await self.store.update(1,screening=settings.screening,reaction_roles=[{"channel_id":5,"message_id":40,"role_id":20,"emoji":"☕","remove_on_unreact":True}])
        body["version"]=2
        with self.assertRaisesRegex(ValueError,"already mapped"):await self.publisher.publish(self.guild,self.actor,body)
        self.channel.send.assert_not_awaited()

    async def test_server_mapping_budget(self):
        mappings=[{"channel_id":5,"message_id":500+n,"role_id":200+n,"emoji":"☕","remove_on_unreact":True} for n in range(24)]
        await self.store.update(1,reaction_roles=mappings)
        with self.assertRaisesRegex(ValueError,"25 reaction-role"):
            await self.publisher.publish(self.guild,self.actor,draft(version=1))
        self.guild.create_role.assert_not_awaited()

    async def test_stale_version_does_not_create_anything(self):
        await self.store.update(1,enabled=True)
        with self.assertRaises(SettingsConflict):await self.publisher.publish(self.guild,self.actor,draft())
        self.guild.create_role.assert_not_awaited();self.channel.send.assert_not_awaited()

    async def test_concurrent_settings_change_rolls_back_only_new_resources(self):
        async def send(**kwargs):
            await self.store.update(1,spam_limit=9)
            return self.message
        self.channel.send.side_effect=send
        result=await self.publisher.publish(self.guild,self.actor,draft())
        self.assertEqual(result["state"],"failed");self.message.delete.assert_awaited_once()
        for role in self.roles[2:]:role.delete.assert_awaited_once()
        self.roles[1].delete.assert_not_awaited()
        settings,version=await self.store.snapshot(1)
        self.assertEqual((settings.spam_limit,settings.reaction_roles,version),(9,[],1))
        self.assertEqual((await self.store.template_operation(1,"a"*32))["state"],"failed")

    async def test_partial_role_creation_failure_is_rolled_back_and_not_replayed(self):
        create=self.guild.create_role.side_effect
        async def fail_second(**kwargs):
            if len(self.roles)>2:raise discord.Forbidden(SimpleNamespace(status=403,reason="Forbidden"),"Forbidden")
            return await create(**kwargs)
        self.guild.create_role.side_effect=fail_second
        result=await self.publisher.publish(self.guild,self.actor,draft())
        self.assertEqual(result["state"],"failed");self.roles[2].delete.assert_awaited_once()
        self.channel.send.assert_not_awaited()
        again=await self.publisher.publish(self.guild,self.actor,draft())
        self.assertEqual(again,result);self.assertEqual(self.guild.create_role.await_count,2)

    async def test_failed_cleanup_reports_the_resource_without_removing_existing_role(self):
        self.channel.send.side_effect=TimeoutError()
        create=self.guild.create_role.side_effect
        async def bad_cleanup(**kwargs):
            role=await create(**kwargs);role.delete.side_effect=TimeoutError();return role
        self.guild.create_role.side_effect=bad_cleanup
        result=await self.publisher.publish(self.guild,self.actor,draft(options=[{"name":"New","emoji":"⭐","role_id":None},{"name":"Existing","emoji":"☕","role_id":"20"}]))
        self.assertEqual(result["state"],"failed");self.assertIn("Could not remove",result["warnings"][0])
        self.roles[1].delete.assert_not_awaited();self.assertFalse((await self.store.settings(1)).reaction_roles)

    async def test_reaction_failure_keeps_saved_mappings_and_returns_warning(self):
        self.message.add_reaction.side_effect=discord.Forbidden(SimpleNamespace(status=403,reason="Forbidden"),"Forbidden")
        result=await self.publisher.publish(self.guild,self.actor,draft())
        self.assertEqual(result["state"],"complete");self.assertTrue(result["warnings"])
        self.assertEqual(len((await self.store.settings(1)).reaction_roles),2);self.message.delete.assert_not_awaited()

    async def test_interrupted_journal_will_not_replay_and_is_deleted_with_guild(self):
        body=draft();operation={"actor_id":77,"fingerprint":"test","state":"posting"}
        import hashlib,json
        operation["fingerprint"]=hashlib.sha256(json.dumps(validate_request(body),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        await self.store.begin_template_operation(1,body["request_id"],0,operation)
        result=await self.publisher.publish(self.guild,self.actor,body)
        self.assertEqual(result["state"],"interrupted");self.guild.create_role.assert_not_awaited()
        await self.store.delete_guild(1);self.assertIsNone(await self.store.template_operation(1,body["request_id"]))

    async def test_validation_catalog_custom_options_and_duplicate_reactions(self):
        self.assertTrue({"gender","pronouns","countries","interests","notifications","colors"} <= {item["id"] for item in CATALOG})
        for template in CATALOG:
            validate_request(draft(template_id=template["id"],title=template["title"],description=template["description"],options=[{"name":name,"emoji":emoji,"role_id":None} for emoji,name in template["options"]]))
        bad=draft();bad["options"][1]["emoji"]=bad["options"][0]["emoji"]
        for body in (bad,draft(title="@everyone"),draft(channel_id=5),draft(options=[]),draft(request_id="bad"),draft(template_id="unknown"),draft(options=[{"name":"a","emoji":"word","role_id":None}])):
            with self.assertRaises(ValueError):validate_request(deepcopy(body))

    async def test_successful_request_remains_idempotent_after_database_reopen(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as folder:
            store=Store(str(Path(folder)/"templates.sqlite3"));await store.open()
            try:
                result=await TemplatePublisher(store,self.bot).publish(self.guild,self.actor,draft())
            finally:
                await store.close()
            reopened=Store(str(Path(folder)/"templates.sqlite3"));await reopened.open()
            try:
                again=await TemplatePublisher(reopened,self.bot).publish(self.guild,self.actor,draft())
                self.assertEqual(result,again);self.channel.send.assert_awaited_once()
            finally:
                await reopened.close()

    async def test_audit_failure_rolls_back_settings_and_success_record_together(self):
        await self.store.db.execute("CREATE TRIGGER fail_template_audit BEFORE INSERT ON settings_audit BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        await self.store.db.commit()
        result=await self.publisher.publish(self.guild,self.actor,draft())
        self.assertEqual(result["state"],"failed");self.message.delete.assert_awaited_once()
        self.assertFalse((await self.store.settings(1)).reaction_roles)
        self.assertEqual((await self.store.snapshot(1))[1],0)
        self.assertEqual((await self.store.template_operation(1,"a"*32))["state"],"failed")

    async def test_twenty_options_fit_discord_embed_field_limits(self):
        options=[{"name":str(i)+"*"*58,"emoji":chr(0x1F600+i),"role_id":None} for i in range(20)]
        result=await self.publisher.publish(self.guild,self.actor,draft(options=options))
        self.assertEqual(result["state"],"complete")
        embed=self.channel.send.await_args.kwargs["embed"]
        self.assertLessEqual(len(embed),6000)
        self.assertTrue(all(len(field.value)<=1024 for field in embed.fields))
