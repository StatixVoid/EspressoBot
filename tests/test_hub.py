import asyncio
from datetime import date, datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord

from espresso.bot import Espresso
from espresso.hub_config import default_hub, validate_hub
from espresso.hub_social import SuggestionView
from espresso.hub_store import HUB_TABLES
from espresso.store import Store, SettingsConflict


class HubSettingsTests(unittest.TestCase):
    def test_disabled_defaults_and_strict_validation(self):
        self.assertTrue(all(value is False for key,value in default_hub().items() if key.endswith('_enabled')))
        for raw in ({'unknown':True},{'voice_user_limit':True},{'birthday_hour':24},{'timezone':'Not/AZone'},
                    {'ticket_categories':[]},{'highlight_source_ids':['oops']},{'tickets_enabled':True},
                    {'highlights_enabled':True,'highlight_channel_id':20,'highlight_source_ids':[20]}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):validate_hub(raw)
        self.assertEqual(validate_hub({'event_role_id':'30'})['event_role_id'],30)


class HubStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store=Store(':memory:');await self.store.open()

    async def asyncTearDown(self):
        await self.store.close()

    async def test_settings_are_versioned_and_server_scoped(self):
        await self.store.save_hub_settings(1,10,0,default_hub()|{'profiles_enabled':True})
        self.assertEqual((await self.store.hub_settings(1))[1],1)
        self.assertFalse((await self.store.hub_settings(2))[0]['profiles_enabled'])
        with self.assertRaises(SettingsConflict):await self.store.save_hub_settings(1,10,0,default_hub())

    async def test_room_intake_serializes_duplicates_and_request_ids(self):
        first,created=await self.store.begin_hub_item(1,'ticket',10,{'subject':'Help'},'request')
        second,again=await self.store.begin_hub_item(1,'ticket',10,{'subject':'Help'},'request')
        self.assertTrue(created);self.assertFalse(again);self.assertEqual(first['id'],second['id'])
        with self.assertRaises(ValueError):await self.store.begin_hub_item(1,'ticket',10,{'subject':'Changed'},'request')
        with self.assertRaises(ValueError):await self.store.begin_hub_item(1,'ticket',10,{'subject':'Another'},'new')
        self.assertIsNone(await self.store.hub_item(2,first['id']))
        with self.assertRaises(SettingsConflict):await self.store.update_hub_item(1,first['id'],version=0,status='closed')
        await self.store.update_hub_item(1,first['id'],status='closed')
        await self.store.begin_hub_item(1,'ticket',10,{},'new')

    async def test_vote_toggle_replace_and_closed_voting(self):
        row,_=await self.store.begin_hub_item(1,'suggestion',10,{'idea':'Game night'},'suggestion')
        await self.store.update_hub_item(1,row['id'],status='open')
        self.assertEqual(await self.store.vote_suggestion(1,row['id'],11,1),{'up':1,'down':0})
        self.assertEqual(await self.store.vote_suggestion(1,row['id'],11,-1),{'up':0,'down':1})
        self.assertEqual(await self.store.vote_suggestion(1,row['id'],11,-1),{'up':0,'down':0})
        with self.assertRaises(ValueError):await self.store.vote_suggestion(2,row['id'],11,1)
        await self.store.update_hub_item(1,row['id'],status='completed')
        with self.assertRaises(ValueError):await self.store.vote_suggestion(1,row['id'],11,1)

    async def test_suggestion_daily_limit_and_cross_server_independence(self):
        for i in range(3):await self.store.begin_hub_item(1,'suggestion',10,{'idea':str(i)},str(i))
        with self.assertRaises(ValueError):await self.store.begin_hub_item(1,'suggestion',10,{},'fourth')
        await self.store.begin_hub_item(2,'suggestion',10,{},'fourth')

    async def test_thanks_limits_cannot_be_reset_by_profile_deletion(self):
        for user in (10,11,12,13,14):await self.store.save_profile(1,user,'','rose')
        self.assertTrue(await self.store.give_thanks(1,10,11,'one',now=100000))
        self.assertFalse(await self.store.give_thanks(1,10,11,'one',now=100001))
        with self.assertRaises(ValueError):await self.store.give_thanks(1,10,10,'self',now=100002)
        with self.assertRaises(ValueError):await self.store.give_thanks(1,10,11,'again',now=100002)
        await self.store.delete_profile(1,10);await self.store.save_profile(1,10,'','rose')
        with self.assertRaises(ValueError):await self.store.give_thanks(1,10,11,'reset',now=100003)
        await self.store.give_thanks(1,10,12,'two',now=100004);await self.store.give_thanks(1,10,13,'three',now=100005)
        with self.assertRaises(ValueError):await self.store.give_thanks(1,10,14,'four',now=100006)
        self.assertEqual((await self.store.profile(1,11))['thanks'],0)
        await self.store.give_thanks(1,10,14,'tomorrow',now=186406)

    async def test_concurrent_thanks_never_exceed_three(self):
        for user in (10,11,12,13,14):await self.store.save_profile(1,user,'','rose')
        results=await asyncio.gather(*(self.store.give_thanks(1,10,user,str(user)) for user in (11,12,13,14)),return_exceptions=True)
        self.assertEqual(sum(result is True for result in results),3)

    async def test_birthdays_store_no_year_and_handle_leap_day(self):
        await self.store.birthday(1,10,2,29)
        self.assertEqual((await self.store.birthdays_on(1,date(2027,2,28)))[0],{'user_id':10,'month':2,'day':29})
        self.assertEqual(await self.store.birthdays_on(1,date(2028,2,28)),[])
        self.assertEqual(len(await self.store.birthdays_on(1,date(2028,2,29))),1)
        with self.assertRaises(ValueError):await self.store.birthday(1,10,4,31)
        await self.store.birthday(1,10)
        self.assertEqual(await self.store.birthdays_on(1,date(2027,2,28)),[])

    async def test_faq_conflicts_badges_and_optout_are_scoped(self):
        await self.store.save_faq(1,10,'rules','Read the rules.','Server rules',0)
        with self.assertRaises(SettingsConflict):await self.store.save_faq(1,11,'rules','Changed','',0)
        self.assertEqual(await self.store.faq_entries(2),[])
        with self.assertRaises(ValueError):await self.store.badge(1,10,'Host',11)
        await self.store.save_profile(1,10,'Hi','lavender');await self.store.badge(1,10,'Host',11)
        self.assertEqual((await self.store.profile(1,10))['badges'],['Host'])
        await self.store.highlight_optout(1,10,True)
        self.assertFalse(await self.store.highlight_optout(2,10))

    async def test_restart_does_not_replay_unconfirmed_deliveries(self):
        with TemporaryDirectory() as folder:
            other=Store(str(Path(folder)/'test.db'));await other.open()
            row,_=await other.begin_hub_item(1,'suggestion',10,{},'uncertain')
            await other.claim_hub_delivery(1,'birthday:today:10');await other.close()
            await other.open()
            self.assertEqual((await other.hub_item(1,row['id']))['status'],'uncertain')
            self.assertFalse(await other.claim_hub_delivery(1,'birthday:today:10'))
            self.assertEqual((await other.hub_deliveries(1))[0]['status'],'uncertain')
            await other.close()

    async def test_closed_transcripts_expire_and_guild_cleanup_covers_all_tables(self):
        row,_=await self.store.begin_hub_item(1,'ticket',10,{'transcript':'PRIVATE'},'ticket')
        await self.store.update_hub_item(1,row['id'],status='closed')
        await self.store.purge_hub_private_data(time.time()+31*86400)
        self.assertEqual((await self.store.hub_item(1,row['id']))['data'],{})
        await self.store.save_profile(1,10,'Hi','rose');await self.store.birthday(1,10,1,1)
        await self.store.delete_guild(1)
        for table in HUB_TABLES:
            async with self.store.db.execute(f'SELECT COUNT(*) FROM {table} WHERE guild_id=1') as cursor:
                self.assertEqual((await cursor.fetchone())[0],0,table)

    async def test_transcripts_expire_even_when_room_deletion_is_still_pending(self):
        row,_=await self.store.begin_hub_item(1,'ticket',10,{'subject':'Help','transcript':'PRIVATE','transcript_until':1000},'one')
        await self.store.update_hub_item(1,row['id'],status='closing')
        await self.store.purge_hub_private_data(1001)
        current=await self.store.hub_item(1,row['id'])
        self.assertNotIn('transcript',current['data']);self.assertEqual(current['data']['subject'],'Help')

    async def test_closing_rooms_does_not_bypass_creation_limits(self):
        for kind in ('ticket','voice'):
            for i in range(5):
                row,_=await self.store.begin_hub_item(1,kind,10,{},f'{kind}:{i}')
                await self.store.update_hub_item(1,row['id'],status='removed')
            with self.assertRaises(ValueError):await self.store.begin_hub_item(1,kind,10,{},'too many')


def make_member(guild,user_id,staff=False):
    member=Mock(spec=discord.Member);member.id=user_id;member.guild=guild;member.bot=False;member.pending=False
    member.guild_permissions=discord.Permissions(manage_guild=staff,manage_roles=staff,manage_events=staff)
    member.roles=[];member.display_name=f'Member {user_id}';member.display_avatar=SimpleNamespace(url='https://cdn.discordapp.com/embed/avatars/0.png')
    member.send=AsyncMock();member.add_roles=AsyncMock();member.remove_roles=AsyncMock();member.move_to=AsyncMock()
    return member


def make_channel(guild,kind,channel_id):
    channel=Mock(spec=kind);channel.id=channel_id;channel.guild=guild;channel.name=f'channel-{channel_id}'
    channel.permissions_for.return_value=discord.Permissions.all();channel.overwrites_for.return_value=discord.PermissionOverwrite()
    channel.is_nsfw=Mock(return_value=False);channel.send=AsyncMock(return_value=SimpleNamespace(id=500));channel.delete=AsyncMock();channel.edit=AsyncMock();channel.set_permissions=AsyncMock()
    return channel


class HubManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot=Espresso(':memory:');await self.bot.store.open();self.hub=self.bot.hub_manager
        self.guild=SimpleNamespace(id=1,name='Café',owner_id=100,unavailable=False)
        self.member=make_member(self.guild,10);self.actor=make_member(self.guild,90,True);self.other=make_member(self.guild,11)
        self.guild.me=make_member(self.guild,99,True);self.guild.me.guild_permissions=discord.Permissions.all()
        self.guild.default_role=Mock(spec=discord.Role);self.guild.default_role.id=1
        self.support=Mock(spec=discord.Role);self.support.id=31;self.support.is_default.return_value=False;self.support.managed=False
        self.role=Mock(spec=discord.Role);self.role.id=32;self.role.is_default.return_value=False;self.role.managed=False;self.role.permissions=discord.Permissions.none()
        self.members={10:self.member,11:self.other,90:self.actor,99:self.guild.me}
        self.guild.fetch_member=AsyncMock(side_effect=lambda user_id:self.members[user_id])
        self.channel=make_channel(self.guild,discord.TextChannel,20);self.channel.topic='espresso-ticket:1'
        self.category=make_channel(self.guild,discord.CategoryChannel,40)
        self.lobby=make_channel(self.guild,discord.VoiceChannel,41);self.lobby.voice_states={10:object()};self.lobby.overwrites={};self.lobby.category_id=40
        self.voice=make_channel(self.guild,discord.VoiceChannel,42);self.voice.voice_states={};self.voice.category_id=40
        self.channels={20:self.channel,40:self.category,41:self.lobby,42:self.voice}
        self.guild.fetch_channel=AsyncMock(side_effect=lambda channel_id:self.channels[channel_id]);self.guild.get_channel=Mock(side_effect=lambda channel_id:self.channels.get(channel_id))
        self.guild.fetch_channels=AsyncMock(return_value=list(self.channels.values()));self.guild.fetch_roles=AsyncMock(return_value=[self.support,self.role])
        self.guild.create_text_channel=AsyncMock(return_value=self.channel);self.guild.create_voice_channel=AsyncMock(return_value=self.voice)
        self.bot.get_guild=Mock(return_value=self.guild);self.bot.log_action=AsyncMock()

    async def asyncTearDown(self):
        await self.bot.close()

    async def enable(self,**changes):
        policy,version=await self.bot.store.hub_settings(1)
        await self.bot.store.save_hub_settings(1,90,version,policy|changes)

    async def test_ticket_room_is_private_and_not_claimable_by_members(self):
        await self.enable(tickets_enabled=True,ticket_category_id=40,support_role_id=31)
        row=await self.hub.open_ticket(self.guild,10,'General','Help with my account','one')
        kwargs=self.guild.create_text_channel.await_args.kwargs
        self.assertFalse(kwargs['overwrites'][self.guild.default_role].view_channel)
        self.assertTrue(kwargs['overwrites'][self.member].view_channel)
        self.assertTrue(kwargs['overwrites'][self.support].view_channel)
        with self.assertRaises(ValueError):await self.hub.claim_ticket(self.guild,10,row['id'])
        with self.assertRaises(ValueError):await self.hub.close_ticket(self.guild,11,row['id'],'No access')
        await self.hub.claim_ticket(self.guild,90,row['id'])
        self.assertEqual((await self.bot.store.hub_item(1,row['id']))['data']['assigned_id'],90)

    async def test_ticket_transcript_saved_before_room_deletion_and_access_is_private(self):
        await self.enable(tickets_enabled=True,ticket_category_id=40,support_role_id=31,ticket_transcripts=True)
        row=await self.hub.open_ticket(self.guild,10,'General','Question','one',save_transcript=True)
        message=SimpleNamespace(author=self.member,content='PRIVATE SUPPORT MESSAGE',created_at=datetime.now(timezone.utc),attachments=[])
        async def history(**kwargs):yield message
        self.channel.history=history
        async def before_delete(**kwargs):
            self.assertIn('PRIVATE SUPPORT MESSAGE',(await self.bot.store.hub_item(1,row['id']))['data']['transcript'])
        self.channel.delete.side_effect=before_delete
        await self.hub.close_ticket(self.guild,10,row['id'],'Solved')
        self.assertEqual((await self.bot.store.hub_item(1,row['id']))['status'],'closed')
        with self.assertRaises(ValueError):await self.hub.ticket_transcript(self.guild,11,row['id'])
        transcript=await self.hub.ticket_transcript(self.guild,10,row['id']);self.assertEqual(transcript.filename,'ticket-1.txt');transcript.close()

    async def test_server_permission_does_not_enable_transcripts_without_member_request(self):
        await self.enable(tickets_enabled=True,ticket_category_id=40,support_role_id=31,ticket_transcripts=True)
        row=await self.hub.open_ticket(self.guild,10,'General','Question','one')
        self.assertFalse(row['data']['transcripts'])
        self.assertIn('disabled for this ticket',self.channel.send.await_args.kwargs['embed'].description)

    async def test_unreadable_requested_transcript_leaves_room_intact(self):
        await self.enable(tickets_enabled=True,ticket_category_id=40,support_role_id=31,ticket_transcripts=True)
        row=await self.hub.open_ticket(self.guild,10,'General','Question','one',save_transcript=True)
        async def history(**kwargs):
            raise discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),'Forbidden')
            yield
        self.channel.history=history
        with self.assertRaises(discord.Forbidden):await self.hub.close_ticket(self.guild,10,row['id'],'Solved')
        self.channel.delete.assert_not_awaited();self.assertEqual((await self.bot.store.hub_item(1,row['id']))['status'],'closing')

    async def test_ticket_uncertain_create_is_not_duplicated_and_can_be_recovered(self):
        await self.enable(tickets_enabled=True,ticket_category_id=40,support_role_id=31)
        self.guild.create_text_channel.side_effect=discord.HTTPException(SimpleNamespace(status=500,reason='Unknown'),'Unknown')
        with self.assertRaises(discord.HTTPException):await self.hub.open_ticket(self.guild,10,'General','Help','one')
        row=await self.hub.open_ticket(self.guild,10,'General','Help','one')
        self.assertEqual(row['status'],'uncertain');self.guild.create_text_channel.assert_awaited_once()
        await self.hub.reconcile_tickets(self.guild,time.time())
        self.assertEqual((await self.bot.store.hub_item(1,row['id']))['channel_id'],20)

    async def test_voice_owner_controls_and_cleanup_only_known_empty_rooms(self):
        await self.enable(voice_enabled=True,voice_lobby_id=41,voice_category_id=40)
        await self.hub.voice_changed(self.member,SimpleNamespace(channel=None),SimpleNamespace(channel=self.lobby))
        self.member.move_to.assert_awaited_once_with(self.voice,reason='Espresso personal voice room')
        row=(await self.bot.store.hub_items(1,'voice'))[0]
        with self.assertRaises(ValueError):await self.hub.edit_room(self.guild,11,name='Not mine')
        await self.hub.edit_room(self.guild,10,name='Game café',user_limit=7,locked=True)
        self.assertFalse(self.voice.set_permissions.await_args.kwargs['overwrite'].connect)
        self.voice.voice_states={11:object()}
        await self.hub.cleanup_voice(self.guild,time.time());self.voice.delete.assert_not_awaited()
        self.voice.voice_states={}
        await self.hub.cleanup_voice(self.guild,time.time());self.voice.delete.assert_awaited_once();self.lobby.delete.assert_not_awaited()
        self.assertEqual((await self.bot.store.hub_item(1,row['id']))['status'],'removed')

    async def test_birthday_is_once_per_local_date_and_suppresses_notifications(self):
        await self.enable(birthdays_enabled=True,birthday_channel_id=20,timezone='America/Phoenix',birthday_hour=9)
        await self.bot.store.birthday(1,10,10,18)
        now=datetime(2026,10,18,16,tzinfo=timezone.utc).timestamp()
        await self.hub.tick_birthdays(self.guild,now);await self.hub.tick_birthdays(self.guild,now+60)
        self.channel.send.assert_awaited_once();self.assertFalse(self.channel.send.await_args.kwargs['allowed_mentions'].users)
        self.assertIn('Member 10',self.channel.send.await_args.kwargs['embed'].description)

    async def test_event_reminder_closed_dms_never_replayed_and_unsubscribe_works_when_disabled(self):
        await self.enable(events_enabled=True)
        now=time.time();data={'name':'Game night','start_at':now+600,'end_at':now+3600,'event_id':700,'role_id':None}
        row,_=await self.bot.store.begin_hub_item(1,'event',90,data,'event')
        await self.bot.store.update_hub_item(1,row['id'],status='scheduled');await self.bot.store.set_rsvp(1,row['id'],10,True)
        event=SimpleNamespace(id=700,name='Game night',start_time=datetime.fromtimestamp(now+600,timezone.utc),end_time=datetime.fromtimestamp(now+3600,timezone.utc),status=discord.EventStatus.scheduled)
        self.guild.fetch_scheduled_event=AsyncMock(return_value=event)
        self.member.send.side_effect=discord.Forbidden(SimpleNamespace(status=403,reason='Closed'),'Closed')
        await self.hub.tick_events(self.guild,now);await self.hub.tick_events(self.guild,now+30)
        self.member.send.assert_awaited_once();self.assertEqual((await self.bot.store.hub_deliveries(1))[0]['status'],'failed')
        await self.enable(events_enabled=False)
        await self.hub.event_rsvp(self.guild,10,row['id'],False)
        self.assertEqual(await self.bot.store.rsvps(1,row['id']),[])

    async def test_native_event_creation_and_cancel_expire_only_its_role_grants(self):
        await self.enable(events_enabled=True)
        event=SimpleNamespace(id=700,status=discord.EventStatus.scheduled,edit=AsyncMock())
        self.guild.create_scheduled_event=AsyncMock(return_value=event);self.guild.fetch_scheduled_event=AsyncMock(return_value=event)
        now=time.time();row=await self.hub.create_event(self.guild,90,'Game night','Bring snacks','Café',now+3600,now+7200,'event')
        self.assertEqual(self.guild.create_scheduled_event.await_args.kwargs['entity_type'],discord.EntityType.external)
        await self.hub.end_event(self.guild,90,row['id'])
        self.assertEqual(event.edit.await_args.kwargs['status'],discord.EventStatus.cancelled)
        self.assertEqual((await self.bot.store.hub_item(1,row['id']))['status'],'cancelled')

    async def test_preexisting_roles_are_preserved_and_owned_roles_expire(self):
        self.hub.community_role=AsyncMock(return_value=self.role)
        self.member.roles=[self.role]
        await self.hub.grant_temporary_role(self.guild,self.member,32,'event:1',time.time()+100)
        self.assertEqual(await self.bot.store.role_grants(1),[]);self.member.add_roles.assert_not_awaited()
        self.member.roles=[]
        await self.hub.grant_temporary_role(self.guild,self.member,32,'event:1',time.time()-1)
        self.member.roles=[self.role]
        await self.hub.cleanup_roles(self.guild,time.time())
        self.member.remove_roles.assert_awaited_once();self.assertEqual(await self.bot.store.role_grants(1),[])

    async def test_suggestion_buttons_persist_and_verification_is_enforced(self):
        self.assertTrue(SuggestionView(self.hub,1).is_persistent())
        await self.enable(suggestions_enabled=True,suggestion_channel_id=20)
        self.member.pending=True
        with self.assertRaises(ValueError):await self.hub.create_suggestion(self.guild,10,'Idea','one')
        self.channel.send.assert_not_awaited()
        self.member.pending=False
        row=await self.hub.create_suggestion(self.guild,10,'Idea','one')
        self.assertEqual(row['status'],'open')
        await self.hub.restore_views()

    async def test_highlights_exclude_author_and_bots_and_remove_on_optout(self):
        await self.enable(highlights_enabled=True,highlight_channel_id=21,highlight_source_ids=[20],highlight_threshold=2)
        target=make_channel(self.guild,discord.TextChannel,21);self.channels[21]=target
        users=[self.member,self.other,make_member(self.guild,12),make_member(self.guild,13)];users[-1].bot=True
        async def voters(**kwargs):
            for user in users:yield user
        reaction=SimpleNamespace(emoji='⭐',users=voters)
        message=SimpleNamespace(id=600,author=self.member,webhook_id=None,content='Lovely moment',jump_url='https://discord.com/channels/1/20/600',reactions=[reaction])
        self.channel.fetch_message=AsyncMock(return_value=message)
        target_message=SimpleNamespace(delete=AsyncMock(),edit=AsyncMock());target.fetch_message=AsyncMock(return_value=target_message)
        await self.hub.refresh_highlight(self.guild,20,600)
        self.assertIn('2 stars',target.send.await_args.kwargs['embed'].title)
        await self.hub.set_highlight_optout(self.guild,10,True)
        target_message.delete.assert_awaited_once()
        await self.hub.refresh_highlight(self.guild,20,600);target.send.assert_awaited_once()

    async def test_highlights_never_copy_private_or_nsfw_content_to_public_board(self):
        await self.enable(highlights_enabled=True,highlight_channel_id=21,highlight_source_ids=[20])
        target=make_channel(self.guild,discord.TextChannel,21);self.channels[21]=target
        self.channel.permissions_for.side_effect=lambda subject:discord.Permissions.none() if subject is self.guild.default_role else discord.Permissions.all()
        self.assertIsNone(await self.hub.highlight_safe(self.guild,await self.hub.policy(1),20,600))
        self.channel.permissions_for.side_effect=None;self.channel.permissions_for.return_value=discord.Permissions.all()
        self.channel.is_nsfw.return_value=True
        self.assertIsNone(await self.hub.highlight_safe(self.guild,await self.hub.policy(1),20,600))
        target.send.assert_not_awaited()

    async def test_optout_recovers_and_deletes_an_uncertain_highlight(self):
        self.bot._connection.user=SimpleNamespace(id=99)
        target=make_channel(self.guild,discord.TextChannel,21);self.channels[21]=target
        row,_=await self.bot.store.begin_hub_item(1,'highlight',10,{'source_channel_id':20,'source_message_id':600},'message:600',21)
        await self.bot.store.update_hub_item(1,row['id'],status='uncertain')
        embed=discord.Embed();embed.add_field(name='Original message',value='[Open in Discord](https://discord.com/channels/1/20/600)')
        post=SimpleNamespace(author=SimpleNamespace(id=99),embeds=[embed],delete=AsyncMock())
        async def history(**kwargs):yield post
        target.history=history
        await self.hub.set_highlight_optout(self.guild,10,True)
        post.delete.assert_awaited_once();self.assertEqual((await self.bot.store.hub_item(1,row['id']))['status'],'removed')

    async def test_temporary_roles_recheck_privileges_and_verification_reservation(self):
        from test_refinements import TestRole
        role=TestRole(32);self.guild.fetch_roles.return_value=[role];self.guild.me.top_role=20;self.actor.top_role=10
        self.assertEqual(await self.hub.community_role(self.guild,32,self.actor),role)
        role.permissions.move_members=True
        with self.assertRaises(ValueError):await self.hub.community_role(self.guild,32,self.actor)
        role.permissions.move_members=False
        from espresso.screening_config import default_screening
        await self.bot.store.update(1,screening=default_screening()|{'verified_role_id':32})
        with self.assertRaises(ValueError):await self.hub.community_role(self.guild,32,self.actor)

    async def test_faq_alias_posts_saved_answer_without_mentions_and_disabled_feature_rejects(self):
        await self.enable(faq_enabled=True)
        await self.bot.store.save_faq(1,90,'rules','Please read #rules. @everyone','Rules',0)
        interaction=SimpleNamespace(guild=self.guild,guild_id=1,user=self.member,response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await self.bot.tree.get_command('tag').callback(interaction,'rules')
        self.assertFalse(interaction.followup.send.await_args.kwargs['allowed_mentions'].everyone)
        await self.enable(faq_enabled=False)
        with self.assertRaises(ValueError):await self.bot.tree.get_command('faq').callback(interaction,'rules')

    async def test_native_transcript_reply_is_ephemeral_and_profile_is_opt_in(self):
        await self.enable(profiles_enabled=True)
        with self.assertRaises(ValueError):await self.hub.profile_embed(self.guild,self.member)
        await self.bot.store.save_profile(1,10,'Hi @everyone','rose')
        embed=await self.hub.profile_embed(self.guild,self.member)
        self.assertNotIn('@everyone',embed.description)
        self.hub.ticket_transcript=AsyncMock(return_value=discord.File(__import__('io').BytesIO(b'Private'),filename='ticket.txt'))
        interaction=SimpleNamespace(guild=self.guild,user=self.member,response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await self.bot.tree.get_command('ticket').get_command('transcript').callback(interaction,1)
        self.assertTrue(interaction.followup.send.await_args.kwargs['ephemeral'])
        self.hub.ticket_transcript.return_value.close()
