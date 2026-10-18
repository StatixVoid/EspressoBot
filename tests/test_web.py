from dataclasses import asdict
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web
import discord

from espresso.config import WebConfig
from espresso.store import Store
from espresso.web import COOKIE, STATE_COOKIE, DiscordAPI, OAuthState, Panel, Session
from espresso.screening_config import default_screening
from espresso.reaction_manager import ReactionRoleManager
from espresso.rules import Settings


class PanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        await self.store.open()
        self.api = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), exchange=AsyncMock(),
                                   user=AsyncMock(return_value={"id":"10", "username":"Harper"}),
                                   guilds=AsyncMock(return_value=[{"id":"1", "name":"The Lounge", "permissions":"32"}]))
        self.api.exchange.return_value = {"access_token":"private-access-token", "expires_in":3600,
                                          "scope":"identify guilds", "token_type":"Bearer"}
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 99
        self.channel.name = "logs"
        self.channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
        self.guild = SimpleNamespace(id=1, unavailable=False, me=Mock(),
                                      fetch_roles=AsyncMock(return_value=[SimpleNamespace(id=1,name="everyone"),SimpleNamespace(id=20,name="Staff")]),
                                      fetch_channels=AsyncMock(return_value=[self.channel]))
        self.bot = SimpleNamespace(is_ready=Mock(return_value=True),
                                    guilds=[self.guild], latency=0.042,
                                    get_guild=Mock(side_effect=lambda id: self.guild if id == 1 else None),
                                    spam=SimpleNamespace(reset_guild=Mock()), log_action=AsyncMock())
        self.bot.reaction_manager = ReactionRoleManager(self.bot)
        self.guild.fetch_member = AsyncMock(return_value=SimpleNamespace(id=10,guild_permissions=SimpleNamespace(manage_roles=False)))
        self.config = WebConfig(client_id="123", client_secret="private-client-secret")
        self.panel = Panel(self.config, self.store, self.bot, self.api)
        self.client = TestClient(TestServer(self.panel.app))
        await self.client.start_server()
        self.panel.sessions["test-session"] = Session("private-access-token", {"id":"10","name":"Harper"}, "csrf-value", time.time()+3600)
        self.client.session.cookie_jar.update_cookies({COOKIE:"test-session"})
        self.headers = {"Origin":"http://localhost:8080", "X-CSRF-Token":"csrf-value"}

    async def asyncTearDown(self):
        await self.client.close()
        await self.store.close()

    async def save(self, changes, version=0, headers=None, guild="1"):
        return await self.client.patch(f"/api/servers/{guild}/settings", json={"version":version,"changes":changes},
                                       headers=self.headers if headers is None else headers)

    async def test_public_pages_and_security_headers(self):
        for path in ("/", "/preview", "/terms-of-service", "/privacy-policy"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual((await self.client.get("/.env")).status, 404)

    async def test_unauthenticated_api_and_dashboard(self):
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get("/api/servers")).status, 401)
        response = await self.client.get("/dashboard", allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/auth/discord")

    async def test_public_stats_follow_live_guild_cache_without_exposing_details(self):
        self.client.session.cookie_jar.clear()
        response = await self.client.get("/api/stats")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = await response.json()
        self.assertEqual(set(data), {"status", "server_count", "latency_ms", "updated_at"})
        self.assertEqual((data["status"], data["server_count"], data["latency_ms"]), ("online", 1, 42))
        self.assertLessEqual(abs(data["updated_at"] - time.time()), 2)
        self.api.guilds.assert_not_awaited()
        self.bot.guilds.append(SimpleNamespace(id=2, name="Private server"))
        self.assertEqual((await (await self.client.get("/api/stats")).json())["server_count"], 2)
        self.bot.guilds.clear()
        self.assertEqual((await (await self.client.get("/api/stats")).json())["server_count"], 0)

    async def test_public_stats_preview_offline_and_unknown_latency(self):
        for latency in (float("nan"), float("inf"), -1):
            self.bot.latency = latency
            data = await (await self.client.get("/api/stats")).json()
            self.assertIsNone(data["latency_ms"])
            self.assertEqual(data["server_count"], 1)
        self.bot.is_ready.return_value = False
        data = await (await self.client.get("/api/stats")).json()
        self.assertEqual((data["status"], data["server_count"], data["latency_ms"]), ("offline", None, None))
        self.panel.bot = None
        data = await (await self.client.get("/api/stats")).json()
        self.assertEqual((data["status"], data["server_count"], data["latency_ms"]), ("preview", None, None))

    async def test_login_state_is_bound_and_one_use(self):
        response = await self.client.get("/auth/discord", allow_redirects=False)
        params = parse_qs(urlsplit(response.headers["Location"]).query)
        self.assertEqual(params["scope"], ["identify guilds"])
        self.assertEqual(params["redirect_uri"], [self.config.callback_url])
        state = params["state"][0]
        self.assertTrue(response.cookies[STATE_COOKIE]["httponly"])
        self.assertEqual(response.cookies[STATE_COOKIE]["samesite"], "Lax")
        bad = await self.client.get(f"/auth/discord/callback?state=wrong&code=x", allow_redirects=False)
        self.assertEqual(bad.status, 400)
        good = await self.client.get(f"/auth/discord/callback?state={state}&code=x", allow_redirects=False)
        self.assertEqual(good.status, 302)
        self.assertEqual(good.headers["Location"], "/dashboard")
        self.assertNotIn("test-session", self.panel.sessions)
        replay = await self.client.get(f"/auth/discord/callback?state={state}&code=x", allow_redirects=False)
        self.assertEqual(replay.status, 400)
        self.api.exchange.assert_awaited_once()

    async def test_install_returns_to_authenticated_dashboard_with_bound_state(self):
        response = await self.client.get("/invite?guild_id=1", allow_redirects=False)
        params = parse_qs(urlsplit(response.headers["Location"]).query)
        self.assertEqual(set(params["scope"][0].split()), {"identify", "guilds", "bot", "applications.commands"})
        self.assertEqual(params["guild_id"], ["1"])
        self.assertEqual(params["disable_guild_select"], ["true"])
        self.assertEqual(params["redirect_uri"], [self.config.callback_url])
        self.assertFalse(discord.Permissions(int(params["permissions"][0])).administrator)
        state = params["state"][0]
        callback = await self.client.get(f"/auth/discord/callback?state={state}&code=x&guild_id=1", allow_redirects=False)
        self.assertEqual(callback.headers["Location"], "/dashboard?server=1")
        self.assertIn(COOKIE, callback.cookies)
        self.assertEqual((await self.client.get(f"/auth/discord/callback?state={state}&code=x")).status, 400)

    async def test_invite_without_oauth_secret_keeps_basic_install_link(self):
        self.panel.config = WebConfig(client_id="123")
        response = await self.client.get("/invite", allow_redirects=False)
        params = parse_qs(urlsplit(response.headers["Location"]).query)
        self.assertEqual(params["scope"], ["bot applications.commands"])
        self.assertNotIn("state", params)

    async def test_setup_requires_login_and_settings_completion_persists(self):
        initial = await (await self.client.get("/api/servers/1/settings")).json()
        self.assertFalse(initial["settings"]["setup_completed"])
        self.assertTrue(initial["channels"][0]["can_log"])
        self.channel.permissions_for.return_value.embed_links = False
        denied_channel = await (await self.client.get("/api/servers/1/settings")).json()
        self.assertFalse(denied_channel["channels"][0]["can_log"])
        self.channel.permissions_for.return_value.embed_links = True
        response = await self.save({"setup_completed":True,"log_channel":"99","enabled":False})
        self.assertEqual(response.status,200)
        self.assertTrue((await self.store.settings(1)).setup_completed)
        self.assertFalse((await self.store.settings(1)).enabled)
        self.assertEqual((await self.save({"setup_completed":"true"},version=1)).status,400)
        self.assertEqual((await self.save({"setup_completed":False},version=0)).status,409)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get("/setup",allow_redirects=False)).headers["Location"],"/auth/discord")

    async def test_install_rejects_invalid_server_selection(self):
        self.assertEqual((await self.client.get("/invite?guild_id=not-a-server",allow_redirects=False)).status,400)
        self.api.exchange.assert_not_awaited()

    async def test_state_requires_browser_cookie_and_expiry(self):
        self.panel.states["valid-state"] = OAuthState(time.time() + 100)
        response = await self.client.get("/auth/discord/callback?state=valid-state&code=x")
        self.assertEqual(response.status, 400)
        self.client.session.cookie_jar.update_cookies({STATE_COOKIE:"valid-state"})
        self.panel.states["valid-state"] = OAuthState(time.time() - 1)
        self.assertEqual((await self.client.get("/auth/discord/callback?state=valid-state&code=x")).status, 400)
        self.api.exchange.assert_not_awaited()

    async def test_setup_server_selection_survives_login_without_trusting_callback_hint(self):
        self.client.session.cookie_jar.clear()
        response = await self.client.get("/setup?server=1",allow_redirects=False)
        self.assertEqual(response.headers["Location"],"/auth/discord?server=1")
        response = await self.client.get(response.headers["Location"],allow_redirects=False)
        state = parse_qs(urlsplit(response.headers["Location"]).query)["state"][0]
        callback = await self.client.get(f"/auth/discord/callback?state={state}&code=x&guild_id=2",allow_redirects=False)
        self.assertEqual(callback.headers["Location"],"/dashboard?server=1")

    async def test_secure_cookie_on_production_origin(self):
        self.panel.config = WebConfig(base_url="https://espresso.harperurias.com", client_id="123", client_secret="secret")
        response = await self.client.get("/auth/discord", allow_redirects=False)
        self.assertTrue(response.cookies[STATE_COOKIE]["secure"])
        self.assertIn("Strict-Transport-Security", response.headers)

    async def test_session_response_never_exposes_access_token(self):
        response = await self.client.get("/api/session")
        data = await response.json()
        self.assertEqual(data["user"]["id"], "10")
        self.assertNotIn("private-access-token", await response.text())

    async def test_server_list_and_fresh_permission_revocation(self):
        response = await self.client.get("/api/servers")
        self.assertEqual((await response.json())["servers"][0]["installed"], True)
        self.api.guilds.return_value[0]["permissions"] = "0"
        denied = await self.save({"enabled":True})
        self.assertEqual(denied.status, 403)
        self.assertFalse((await self.store.settings(1)).enabled)

    async def test_first_dashboard_load_after_each_login_uses_one_fresh_guild_request(self):
        for attempt in range(2):
            self.client.session.cookie_jar.clear()
            login = await self.client.get("/auth/discord",allow_redirects=False)
            state = parse_qs(urlsplit(login.headers["Location"]).query)["state"][0]
            callback = await self.client.get(f"/auth/discord/callback?state={state}&code=code-{attempt}",allow_redirects=False)
            self.assertEqual(callback.status,302)
            self.assertEqual((await self.client.get("/api/session")).status,200)
            self.api.guilds.reset_mock()
            self.api.guilds.side_effect = [[{"id":"1","name":"The Lounge","permissions":"32"}],web.HTTPServiceUnavailable(reason="Duplicate immediate permission fetch")]
            response = await self.client.get("/api/servers?include_settings=1")
            self.assertEqual(response.status,200,await response.text())
            data = await response.json()
            self.assertEqual(data["initial_server"]["id"],"1")
            self.assertEqual(data["initial_server"]["data"]["channels"][0]["id"],"99")
            self.api.guilds.assert_awaited_once()
        self.api.guilds.side_effect = None
        self.api.guilds.return_value = [{"id":"1","name":"The Lounge","permissions":"0"}]
        self.assertEqual((await self.client.get("/api/servers/1/settings")).status,403)

    async def test_bootstrap_never_reads_a_server_missing_from_current_permissions(self):
        response = await self.client.get("/api/servers?include_settings=1&server=2")
        self.assertEqual((await response.json())["initial_server"]["id"],"1")
        self.api.guilds.return_value[0]["permissions"] = "0"
        response = await self.client.get("/api/servers?include_settings=1&server=1")
        data = await response.json()
        self.assertEqual(data["servers"],[])
        self.assertIsNone(data["initial_server"])

    async def test_bootstrap_preserves_server_choices_when_settings_fetch_fails(self):
        self.guild.fetch_roles.side_effect = discord.HTTPException(SimpleNamespace(status=503,reason="Unavailable"),"Unavailable")
        response = await self.client.get("/api/servers?include_settings=1")
        self.assertEqual(response.status,200)
        data = await response.json()
        self.assertEqual(data["servers"][0]["id"],"1")
        self.assertIn("Could not read",data["initial_server"]["error"])
        self.assertNotIn("data",data["initial_server"])
        self.api.guilds.assert_awaited_once()

    async def test_bootstrap_offline_and_invalid_hint(self):
        self.bot.is_ready.return_value = False
        data = await (await self.client.get("/api/servers?include_settings=1")).json()
        self.assertIsNone(data["initial_server"])
        self.guild.fetch_roles.assert_not_awaited()
        self.assertEqual((await self.client.get("/api/servers?include_settings=1&server=bad")).status,400)

    async def test_cross_server_access_is_denied(self):
        self.assertEqual((await self.client.get("/api/servers/2/settings")).status, 403)
        self.assertEqual((await self.save({"enabled":True}, guild="2")).status, 403)

    async def test_save_persists_normalizes_and_audits(self):
        response = await self.save({"enabled":True,"bad_words":[" TESTword ","testword"],"exempt_roles":["20"],"log_channel":"99"})
        self.assertEqual(response.status, 200, await response.text())
        data = await response.json()
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["settings"]["bad_words"], ["testword"])
        self.assertEqual(data["settings"]["exempt_roles"], ["20"])
        self.assertTrue((await self.store.settings(1)).enabled)
        async with self.store.db.execute("SELECT actor_id,changed_keys FROM settings_audit") as cur:
            row = await cur.fetchone()
        self.assertEqual(row[0],10)
        self.assertIn("enabled",row[1])
        self.bot.spam.reset_guild.assert_called_once_with(1)

    async def test_activity_is_private_paged_and_rechecks_server_access(self):
        for i in range(55):
            await self.store.log_activity(1,"Automatic filter",f"Member {i}")
        await self.store.log_activity(2,"Settings changed","Private other-server activity")
        response = await self.client.get("/api/servers/1/logs")
        data = await response.json()
        self.assertEqual(len(data["logs"]),50)
        self.assertIsInstance(data["logs"][0]["id"],str)
        self.assertNotIn("Private other-server",await response.text())
        more = await (await self.client.get("/api/servers/1/logs?before="+data["next_cursor"])).json()
        self.assertEqual(len(more["logs"]),5)
        self.assertEqual((await self.client.get("/api/servers/1/logs?kind=unknown")).status,400)
        self.assertEqual((await self.client.get("/api/servers/2/logs")).status,403)
        self.api.guilds.return_value[0]["permissions"] = "0"
        self.assertEqual((await self.client.get("/api/servers/1/logs")).status,403)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get("/api/servers/1/logs")).status,401)

    async def test_screening_ids_validation_and_review_csrf(self):
        manager = SimpleNamespace(validate=AsyncMock(),review=AsyncMock(return_value={"status":"approved"}),publish=AsyncMock(return_value="https://discord.com/channels/1/2/3"))
        self.bot.screening_manager = manager
        policy = {**default_screening(),"enabled":True,"verified_role_id":"20","entry_channel_id":"99"}
        response = await self.save({"screening":policy})
        self.assertEqual(response.status,200,await response.text())
        self.assertEqual((await response.json())["settings"]["screening"]["verified_role_id"],"20")
        manager.validate.assert_awaited_once()
        self.assertEqual((await self.client.post("/api/servers/1/screening/20",json={"action":"approve","version":1})).status,403)
        self.assertEqual((await self.client.post("/api/servers/2/screening/20",json={"action":"approve","version":1},headers=self.headers)).status,403)
        self.assertEqual((await self.client.post("/api/servers/1/screening/20",json={"action":"approve","version":True},headers=self.headers)).status,400)
        manager.review.assert_not_awaited()
        response = await self.client.post("/api/servers/1/screening/20",json={"action":"approve","version":1},headers=self.headers)
        self.assertEqual(response.status,200)
        self.assertEqual(manager.review.await_args.args[2:],(20,"approve",1))
        self.assertEqual((await self.client.post("/api/servers/1/screening/publish")).status,403)
        self.assertEqual((await self.client.post("/api/servers/1/screening/publish",headers=self.headers)).status,200)
        self.api.guilds.return_value[0]["permissions"] = "0"
        self.assertEqual((await self.client.get("/api/servers/1/screening")).status,403)

    async def test_screening_errors_do_not_save_settings_or_report_success(self):
        self.bot.screening_manager = SimpleNamespace(validate=AsyncMock(side_effect=ValueError("Enable member events first.")),review=AsyncMock(side_effect=ValueError("Current rules agreement required.")))
        policy = {**default_screening(),"enabled":True,"verified_role_id":"20","entry_channel_id":"99"}
        response = await self.save({"screening":policy})
        self.assertEqual(response.status,400)
        self.assertFalse((await self.store.settings(1)).screening["enabled"])
        response = await self.client.post("/api/servers/1/screening/20",json={"action":"approve","version":1},headers=self.headers)
        self.assertEqual(response.status,400)
        self.assertIn("agreement",(await response.json())["error"])

    async def test_csrf_and_origin_are_required(self):
        for headers in ({},{"Origin":"https://evil.example","X-CSRF-Token":"csrf-value"},
                        {"Origin":self.config.base_url,"X-CSRF-Token":"wrong"}):
            self.assertEqual((await self.save({"enabled":True}, headers=headers)).status, 403)

    async def test_stale_form_cannot_overwrite_slash_changes(self):
        await self.store.update(1, spam_limit=10)
        response = await self.save({"enabled":True}, version=0)
        self.assertEqual(response.status,409)
        self.assertEqual((await self.store.settings(1)).spam_limit,10)
        self.assertFalse((await self.store.settings(1)).enabled)

    async def test_invalid_values_roles_and_channels_never_save(self):
        for changes in ({"spam_limit":True},{"timeout_minutes":40321},{"enabled":"false"},
                        {"exempt_roles":["555"]},{"exempt_roles":["1"]},{"log_channel":"500"},
                        {"log_channel":99},{"new_secret_setting":True},{"bad_words":[1]}):
            with self.subTest(changes=changes):
                response = await self.save(changes)
                self.assertEqual(response.status,400,await response.text())
        self.assertEqual((await self.store.snapshot(1))[1],0)

    async def test_missing_log_permission_and_offline_bot(self):
        self.channel.permissions_for.return_value.embed_links = False
        self.assertEqual((await self.save({"log_channel":"99"})).status,400)
        self.bot.is_ready.return_value = False
        self.assertEqual((await self.save({"enabled":True})).status,503)

    async def test_logout_invalidates_session_and_requires_csrf(self):
        self.assertEqual((await self.client.post("/auth/logout")).status,403)
        self.assertEqual((await self.client.post("/auth/logout",headers=self.headers)).status,200)
        self.assertEqual((await self.client.get("/api/session")).status,401)

    async def test_get_settings_returns_strings_for_discord_ids(self):
        await self.store.update(1,log_channel=99,exempt_roles=[20])
        response = await self.client.get("/api/servers/1/settings")
        data = await response.json()
        self.assertEqual(data["settings"]["log_channel"],"99")
        self.assertEqual(data["roles"][0]["id"],"20")

    async def test_login_rate_limit(self):
        for _ in range(10):
            self.assertEqual((await self.client.get("/auth/discord",allow_redirects=False)).status,302)
        self.assertEqual((await self.client.get("/auth/discord",allow_redirects=False)).status,429)

    async def test_missing_oauth_returns_to_usable_homepage(self):
        self.panel.config = WebConfig()
        response = await self.client.get("/auth/discord",allow_redirects=False)
        self.assertEqual(response.headers["Location"],"/?notice=unavailable")

    async def test_hate_filter_settings_validate_and_normalize(self):
        filters = Settings().hate_filters
        filters["racism"].update(enabled=True,level=4,extra_terms=[" EXTRA PHRASE "])
        response = await self.save({"hate_filters":filters})
        self.assertEqual(response.status,200,await response.text())
        self.assertEqual((await self.store.settings(1)).hate_filters["racism"]["extra_terms"],["extra phrase"])
        filters["racism"]["level"] = 6
        self.assertEqual((await self.save({"hate_filters":filters},version=1)).status,400)

    async def test_reaction_role_edits_require_manage_roles(self):
        response = await self.save({"reaction_roles_enabled":True})
        self.assertEqual(response.status,400,await response.text())
        self.assertIn("Manage Roles",await response.text())
        self.assertFalse((await self.store.settings(1)).reaction_roles_enabled)

    async def test_reaction_mapping_ids_are_parsed_and_seed_outcome_is_reported(self):
        self.bot.reaction_manager.validate = AsyncMock(return_value=[])
        self.bot.reaction_manager.seed = AsyncMock(return_value=["Example reaction could not be added."])
        mapping = {"channel_id":"5","message_id":"50","emoji":"☕","role_id":"20","remove_on_unreact":True}
        response = await self.save({"reaction_roles_enabled":True,"reaction_roles":[mapping]})
        self.assertEqual(response.status,200,await response.text())
        data = await response.json()
        self.assertEqual(data["settings"]["reaction_roles"][0]["message_id"],"50")
        self.assertEqual((await self.store.settings(1)).reaction_roles[0]["message_id"],50)
        self.assertTrue(data["setup_warnings"])

    async def test_invalid_reaction_mapping_cannot_be_saved(self):
        mapping = {"channel_id":"5","message_id":"50","emoji":"text","role_id":"20","remove_on_unreact":True}
        self.assertEqual((await self.save({"reaction_roles":[mapping]})).status,400)


class ConfigTests(unittest.TestCase):
    def test_remote_plain_http_is_rejected(self):
        for url in ("http://espresso.harperurias.com","https://x.example/path","https://user:pass@x.example"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                WebConfig(base_url=url)
