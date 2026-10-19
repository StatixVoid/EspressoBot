import json
import unittest

import test_community_web as fixtures
from espresso.hub_config import default_hub


class HubWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.CommunityWebTests.asyncSetUp(self)
        self.actor.bot=False;self.actor.pending=False
    asyncTearDown=fixtures.CommunityWebTests.asyncTearDown

    async def post(self,path,data,headers=None):
        return await self.client.post('/api/servers/1/hub'+path,json=data,headers=self.headers if headers is None else headers)

    async def test_manager_save_requires_csrf_and_current_version(self):
        value={'settings':default_hub()|{'faq_enabled':True},'version':0}
        self.assertEqual((await self.client.patch('/api/servers/1/hub',json=value)).status,403)
        first=await self.client.patch('/api/servers/1/hub',json=value,headers=self.headers)
        self.assertEqual(first.status,200,await first.text())
        self.assertEqual((await self.client.patch('/api/servers/1/hub',json=value,headers=self.headers)).status,409)
        value['settings']['unknown']=True;value['version']=1
        self.assertEqual((await self.client.patch('/api/servers/1/hub',json=value,headers=self.headers)).status,400)

    async def test_moderator_and_revoked_members_cannot_read_hub_data(self):
        self.panel.api.guilds.return_value=[{'id':'1','name':'Café','permissions':str(1<<40)}]
        for path in ('','/items/ticket','/items/event','/items/suggestion'):
            self.assertEqual((await self.client.get('/api/servers/1/hub'+path)).status,403)
        self.panel.api.guilds.return_value=[{'id':'1','name':'Café','permissions':'32'}]
        self.actor.guild_permissions.manage_guild=False
        self.assertEqual((await self.client.get('/api/servers/1/hub')).status,403)

    async def test_lists_never_expose_transcripts_and_are_server_scoped(self):
        row,_=await self.bot.store.begin_hub_item(1,'ticket',11,{'subject':'Question','transcript':'PRIVATE TRANSCRIPT'},'one')
        await self.bot.store.begin_hub_item(2,'ticket',12,{'subject':'OTHER SERVER'},'one')
        response=await self.client.get('/api/servers/1/hub/items/ticket')
        self.assertEqual(response.status,200);body=await response.text()
        self.assertNotIn('PRIVATE TRANSCRIPT',body);self.assertNotIn('OTHER SERVER',body)
        self.assertEqual((await self.post('/items/'+str(row['id'])+'/action',{'action':'claim','version':0})).status,409)

    async def test_faq_conflict_and_badge_validation(self):
        value={'answer':'Read the rules','summary':'Server rules','version':0}
        url='/api/servers/1/hub/faq/rules'
        self.assertEqual((await self.client.put(url,json=value,headers=self.headers)).status,200)
        self.assertEqual((await self.client.put(url,json=value,headers=self.headers)).status,409)
        self.assertEqual((await self.client.delete(url,json=value)).status,403)
        value['version']=1
        self.assertEqual((await self.client.delete(url,json=value,headers=self.headers)).status,200)
        self.assertEqual((await self.post('/badges',{'member_id':'10','name':'Host','remove':1})).status,400)

    async def test_uncertain_retirement_requires_explicit_checked_state(self):
        row,_=await self.bot.store.begin_hub_item(1,'event',10,{},'one')
        await self.bot.store.update_hub_item(1,row['id'],status='uncertain')
        path=f"/items/{row['id']}/action";value={'action':'retire','version':2}
        self.assertEqual((await self.post(path,value)).status,400)
        value['checked_discord']=True
        self.assertEqual((await self.post(path,value)).status,200)
        self.assertEqual((await self.bot.store.hub_item(1,row['id']))['status'],'removed')

    async def test_hub_endpoints_require_login_and_fresh_oauth_access(self):
        self.panel.api.guilds.return_value=[]
        self.assertEqual((await self.client.get('/api/servers/1/hub/items/voice')).status,403)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get('/api/servers/1/hub')).status,401)
