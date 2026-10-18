from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web

from espresso.web import DiscordAPI


class Response:
    def __init__(self,status,data):
        self.status,self.data=status,data
    async def __aenter__(self):
        return self
    async def __aexit__(self,*args):
        pass
    async def json(self):
        return self.data


class DiscordAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_short_rate_limit_retries_read_without_caching_permissions(self):
        api = DiscordAPI()
        api.http = SimpleNamespace(request=Mock(side_effect=[Response(429,{"retry_after":.01}),Response(200,[{"id":"1","permissions":"32"}]),Response(200,[{"id":"1","permissions":"0"}])]))
        with patch("espresso.web.asyncio.sleep",new_callable=AsyncMock) as sleep:
            self.assertEqual((await api.guilds("private-token"))[0]["permissions"],"32")
            sleep.assert_awaited_once()
        self.assertEqual((await api.guilds("private-token"))[0]["permissions"],"0")
        self.assertEqual(api.http.request.call_count,3)

    async def test_long_rate_limit_has_a_bounded_failure_and_no_retry_storm(self):
        api = DiscordAPI()
        api.http = SimpleNamespace(request=Mock(return_value=Response(429,{"retry_after":30})))
        with patch("espresso.web.asyncio.sleep",new_callable=AsyncMock) as sleep:
            with self.assertRaises(web.HTTPServiceUnavailable):
                await api.request("GET","/users/@me/guilds")
            sleep.assert_not_awaited()
        api.http.request.assert_called_once()

    async def test_only_reads_retry_server_errors_and_authorization_expires_immediately(self):
        api = DiscordAPI()
        api.http = SimpleNamespace(request=Mock(side_effect=[Response(503,{}),Response(200,{"ok":True})]))
        with patch("espresso.web.asyncio.sleep",new_callable=AsyncMock):
            self.assertEqual(await api.request("GET","/users/@me"),{"ok":True})
        for method,status,error in (("POST",503,web.HTTPServiceUnavailable),("GET",401,web.HTTPUnauthorized)):
            api.http.request=Mock(return_value=Response(status,{}))
            with self.assertRaises(error):
                await api.request(method,"/oauth2/token")
            api.http.request.assert_called_once()
