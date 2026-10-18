from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock

from aiohttp.test_utils import TestClient, TestServer
import discord

from espresso.bot import Espresso
from espresso.config import WebConfig
from espresso.web import Panel, Session, COOKIE
from espresso.community_config import default_welcome, default_community


class CommunityWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot=Espresso(":memory:",member_screening_events=True)
        self.bot._connection.user=SimpleNamespace(id=99)
        await self.bot.store.open()
        self.actor=Mock(spec=discord.Member);self.actor.id=10;self.actor.top_role=5
        self.actor.guild_permissions=discord.Permissions(manage_guild=True,moderate_members=True)
        self.guild=SimpleNamespace(id=1,owner_id=100,name="Café",unavailable=False,me=Mock(),default_role=Mock())
        self.guild.me.guild_permissions=discord.Permissions.all()
        self.channel=Mock(spec=discord.TextChannel);self.channel.id=20;self.channel.name="general"
        self.channel.permissions_for.return_value=discord.Permissions.all()
        self.guild.fetch_member=AsyncMock(return_value=self.actor)
        self.guild.fetch_channels=AsyncMock(return_value=[self.channel])
        self.guild.fetch_channel=AsyncMock(return_value=self.channel)
        self.guild.fetch_roles=AsyncMock(return_value=[])
        self.bot.is_ready=Mock(return_value=True);self.bot.get_guild=Mock(return_value=self.guild);self.bot.log_action=AsyncMock()
        api=SimpleNamespace(start=AsyncMock(),close=AsyncMock(),guilds=AsyncMock(return_value=[{"id":"1","name":"Café","permissions":"32"}]))
        self.panel=Panel(WebConfig(),self.bot.store,self.bot,api)
        self.panel.sessions["test"]=Session("test-token",{"id":"10","name":"Staff"},"csrf",time.time()+3600)
        self.client=TestClient(TestServer(self.panel.app));await self.client.start_server()
        self.client.session.cookie_jar.update_cookies({COOKIE:"test"})
        self.headers={"Origin":"http://localhost:8080","X-CSRF-Token":"csrf"}

    async def asyncTearDown(self):
        await self.client.close();await self.bot.close()

    async def post(self,path,body,headers=None):
        return await self.client.post("/api/servers/1/"+path,json=body,headers=self.headers if headers is None else headers)

    async def test_reports_and_cases_require_current_moderation_permission(self):
        await self.bot.store.create_inbox(1,"report",11,"PRIVATE REPORT BODY","report")
        self.actor.guild_permissions.moderate_members=False
        for path in ("inbox","cases","inbox/1/history"):
            response=await self.client.get("/api/servers/1/"+path)
            self.assertEqual(response.status,403);self.assertNotIn("PRIVATE REPORT BODY",await response.text())
        self.actor.guild_permissions.moderate_members=True
        result=await (await self.client.get("/api/servers/1/inbox")).json()
        self.assertEqual(result["items"][0]["body"],"PRIVATE REPORT BODY")

    async def test_new_endpoints_require_login_and_fresh_server_access(self):
        self.panel.api.guilds.return_value=[]
        self.assertEqual((await self.client.get("/api/servers/1/posts")).status,403)
        self.client.session.cookie_jar.clear()
        for path in ("inbox","cases","health","posts"):
            self.assertEqual((await self.client.get("/api/servers/1/"+path)).status,401)

    async def test_review_requires_csrf_and_detects_conflicting_review(self):
        item=await self.bot.store.create_inbox(1,"report",11,"concern","report")
        body={"action":"claim","version":1,"note":""}
        self.assertEqual((await self.post(f"inbox/{item}",body,{})).status,403)
        self.assertEqual((await self.post(f"inbox/{item}",body)).status,200)
        self.assertEqual((await self.post(f"inbox/{item}",body)).status,409)
        self.assertEqual((await self.post(f"inbox/{item}",{"action":"resolve","version":2,"note":""})).status,400)
        self.assertEqual((await self.post(f"inbox/{item}",{"action":"resolve","version":2,"note":"Reviewed privately"})).status,200)
        history=await (await self.client.get(f"/api/servers/1/inbox/{item}/history")).json()
        self.assertEqual(len(history["history"]),2)

    async def test_filter_test_is_read_only_and_validates_channel_access(self):
        await self.bot.store.update(1,enabled=True,bad_words=["testword"])
        response=await self.post("test-filter",{"text":"testword","channel_id":"20"})
        self.assertEqual(response.status,200);self.assertTrue((await response.json())["matched"])
        self.assertEqual(await self.bot.store.cases(1),[])
        self.channel.permissions_for.return_value=discord.Permissions.none()
        self.assertEqual((await self.post("test-filter",{"text":"testword","channel_id":"20"})).status,403)

    async def test_schedule_validation_idempotency_and_cancel(self):
        body={"channel_id":"20","content":"Hello café","due_at":time.time()+60,"repeat_days":0,"notify":False,"request_id":"test-scheduling-id"}
        first=await self.post("posts",body);self.assertEqual(first.status,201);post_id=(await first.json())["id"]
        second=await self.post("posts",body);self.assertEqual((await second.json())["id"],post_id)
        self.assertEqual(len(await self.bot.store.posts(1)),1)
        self.assertEqual((await self.post("posts",body|{"due_at":time.time()-60,"request_id":"other-request"})).status,400)
        self.assertEqual((await self.post(f"posts/{post_id}/cancel",{})).status,200)
        self.assertEqual((await self.post(f"posts/{post_id}/cancel",{})).status,400)

    async def test_extended_settings_save_and_channel_ownership(self):
        changes={"observation_mode":True,"allowed_domains":["example.com"],"warning_expiry_days":30,
                 "welcome":default_welcome()|{"enabled":True,"channel_id":"20"},
                 "channel_rules":[{"channel_id":"20","enabled":True,"block_links":False,"block_invites":None,"spam_enabled":None}]}
        response=await self.client.patch("/api/servers/1/settings",json={"version":0,"changes":changes},headers=self.headers)
        self.assertEqual(response.status,200,await response.text())
        result=await response.json();self.assertEqual(result["settings"]["welcome"]["channel_id"],"20")
        self.assertFalse((await self.bot.store.settings(1)).channel_rules[0]["block_links"])
        changes={"channel_rules":[{"channel_id":"999","enabled":True,"block_links":False,"block_invites":None,"spam_enabled":None}]}
        response=await self.client.patch("/api/servers/1/settings",json={"version":1,"changes":changes},headers=self.headers)
        self.assertEqual(response.status,400)

    async def test_health_and_raid_recovery_stay_private(self):
        response=await self.client.get("/api/servers/1/health")
        self.assertEqual(response.status,200);self.assertTrue((await response.json())["checks"])
        self.assertEqual((await self.post("protection/end",{})).status,200)
        self.assertEqual((await self.post("protection/end",{},{})).status,403)
