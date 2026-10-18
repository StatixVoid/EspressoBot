"""Same-process website and Discord OAuth settings API. Tokens stay in memory."""

from collections import OrderedDict
from dataclasses import asdict, dataclass
import hmac
import logging
import math
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
import discord

from .config import WebConfig
from .rules import Settings, normalize
from .store import SettingsConflict, Store
from .hate_filters import validate_filters

PUBLIC = Path(__file__).with_name("public")
API = "https://discord.com/api/v10"
LOG = logging.getLogger("espresso.web")
COOKIE = "espresso_session"
STATE_COOKIE = "espresso_oauth"
MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8


@dataclass
class Session:
    access_token: str
    user: dict
    csrf: str
    expires: float


@dataclass
class OAuthState:
    expires: float
    guild_id: str | None = None


class DiscordAPI:
    def __init__(self):
        self.http: aiohttp.ClientSession | None = None

    async def start(self):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def close(self):
        if self.http:
            await self.http.close()

    async def request(self, method, path, **kwargs):
        try:
            async with self.http.request(method, API + path, **kwargs) as response:
                if response.status == 401:
                    raise web.HTTPUnauthorized(reason="Discord authorization expired. Sign in again.")
                if response.status >= 400:
                    raise web.HTTPServiceUnavailable(reason="Discord is temporarily unavailable. Please try again.")
                return await response.json()
        except (aiohttp.ClientError, TimeoutError):
            raise web.HTTPServiceUnavailable(reason="Discord could not be reached. Please try again.") from None

    async def exchange(self, code, config):
        return await self.request("POST", "/oauth2/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": config.callback_url,
        }, auth=aiohttp.BasicAuth(config.client_id, config.client_secret))

    async def user(self, token):
        return await self.request("GET", "/users/@me", headers={"Authorization": f"Bearer {token}"})

    async def guilds(self, token):
        guilds = []
        after = "0"
        for _ in range(10):
            page = await self.request("GET", "/users/@me/guilds", params={"limit": "200", "after": after},
                                      headers={"Authorization": f"Bearer {token}"})
            guilds.extend(page)
            if len(page) < 200:
                return guilds
            after = str(max(int(g["id"]) for g in page))
        raise web.HTTPServiceUnavailable(reason="Too many servers to load. Please contact the bot operator.")


def eligible(guild: dict) -> bool:
    return bool(guild.get("owner") or int(guild.get("permissions", 0)) & (MANAGE_GUILD | ADMINISTRATOR))


def settings_json(settings: Settings) -> dict:
    data = asdict(settings)
    data["exempt_roles"] = [str(role) for role in settings.exempt_roles]
    data["log_channel"] = str(settings.log_channel) if settings.log_channel else None
    data["reaction_roles"] = [{**mapping, **{key:str(mapping[key]) for key in ("channel_id", "message_id", "role_id")}}
                              for mapping in settings.reaction_roles]
    return data


def snowflake(value) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**64:
        raise ValueError("Discord IDs must be positive decimal strings.")
    return int(value)


class Panel:
    def __init__(self, config: WebConfig, store: Store, bot=None, api=None):
        self.config, self.store, self.bot = config, store, bot
        self.api = api or DiscordAPI()
        self.sessions: OrderedDict[str, Session] = OrderedDict()
        self.states: OrderedDict[str, OAuthState] = OrderedDict()
        self.login_hits: OrderedDict[str, list[float]] = OrderedDict()
        self.app = web.Application(middlewares=[self.guard], client_max_size=256 * 1024)
        self.app.add_routes([
            web.get("/", self.home), web.get("/dashboard", self.dashboard),
            web.get("/preview", self.dashboard), web.get("/setup", self.dashboard), web.get("/invite", self.invite),
            web.get("/terms-of-service", self.terms), web.get("/privacy-policy", self.privacy),
            web.get("/terms", self.terms), web.get("/privacy", self.privacy),
            web.get("/auth/discord", self.login), web.get("/auth/discord/callback", self.callback),
            web.post("/auth/logout", self.logout), web.get("/api/session", self.session_info),
            web.get("/api/stats", self.stats),
            web.get("/api/servers", self.servers),
            web.get("/api/servers/{guild_id}/settings", self.get_settings),
            web.patch("/api/servers/{guild_id}/settings", self.save_settings),
        ])
        self.app.router.add_static("/assets/", PUBLIC, show_index=False)
        self.app.on_startup.append(self.start)
        self.app.on_cleanup.append(self.cleanup)

    async def start(self, app):
        await self.api.start()

    async def cleanup(self, app):
        self.sessions.clear()
        self.states.clear()
        await self.api.close()

    @web.middleware
    async def guard(self, request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            if exc.status >= 400:
                response = web.json_response({"error": exc.reason}, status=exc.status)
            else:
                response = exc
        except (ValueError, TypeError):
            response = web.json_response({"error": "Invalid request. Check the form values."}, status=400)
        except Exception as exc:
            LOG.error("Web request failed (%s).", type(exc).__name__)
            response = web.json_response({"error": "The request failed. Please try again."}, status=500)
        if isinstance(response, web.HTTPException):
            redirect = web.Response(status=response.status, headers=response.headers, body=response.body)
            redirect.cookies.update(response.cookies)
            response = redirect
        response.headers.update({
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        })
        if self.config.secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def session(self, request) -> Session:
        key = request.cookies.get(COOKIE, "")
        session = self.sessions.get(key)
        if session is None or session.expires <= time.time():
            self.sessions.pop(key, None)
            raise web.HTTPUnauthorized(reason="Please sign in with Discord.")
        return session

    def check_csrf(self, request, session):
        if (request.headers.get("Origin") != self.config.base_url
                or not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf)):
            raise web.HTTPForbidden(reason="This request could not be verified. Reload and try again.")

    def cookie(self, response, name, value, max_age):
        response.set_cookie(name, value, httponly=True, secure=self.config.secure,
                            samesite="Lax", max_age=max_age, path="/")

    async def home(self, request):
        return web.FileResponse(PUBLIC / "index.html")

    async def stats(self, request):
        """Public aggregate gateway stats; never expose server or member details."""
        online = self.bot is not None and self.bot.is_ready()
        latency = self.bot.latency if online else None
        return web.json_response({
            "status": "online" if online else "offline" if self.bot is not None else "preview",
            "server_count": len(self.bot.guilds) if online else None,
            "latency_ms": round(latency * 1000) if latency is not None and math.isfinite(latency) and latency >= 0 else None,
            "updated_at": int(time.time()),
        })

    async def terms(self, request):
        return web.FileResponse(PUBLIC / "terms.html")

    async def privacy(self, request):
        return web.FileResponse(PUBLIC / "privacy.html")

    async def dashboard(self, request):
        if request.path != "/preview":
            try:
                self.session(request)
            except web.HTTPUnauthorized:
                guild_id = request.query.get("server")
                if guild_id:
                    snowflake(guild_id)
                    raise web.HTTPFound("/auth/discord?server=" + guild_id)
                raise web.HTTPFound("/auth/discord")
        return web.FileResponse(PUBLIC / "dashboard.html")

    async def invite(self, request):
        if self.config.oauth_ready:
            return await self.login(request, install=True)
        if not self.config.client_id:
            raise web.HTTPFound("/?notice=unavailable")
        permissions = discord.Permissions(view_channel=True, send_messages=True, read_message_history=True,
                                          manage_messages=True, embed_links=True, moderate_members=True,
                                          send_messages_in_threads=True, add_reactions=True, manage_roles=True)
        params = {"client_id": self.config.client_id, "scope": "bot applications.commands",
                  "permissions": str(permissions.value), "integration_type": "0"}
        guild_id = request.query.get("guild_id")
        if guild_id:
            snowflake(guild_id)
            params.update(guild_id=guild_id, disable_guild_select="true")
        raise web.HTTPFound("https://discord.com/oauth2/authorize?" + urlencode(params))

    async def login(self, request, *, install=False):
        if not self.config.oauth_ready:
            raise web.HTTPFound("/?notice=unavailable")
        now = time.time()
        # Bound memory and login attempts per peer. Proxy headers are not trusted.
        peer = request.remote or "unknown"
        hits = [t for t in self.login_hits.pop(peer, []) if t > now - 60]
        if len(hits) >= 10:
            raise web.HTTPTooManyRequests(reason="Too many sign-in attempts. Please wait a minute.")
        self.login_hits[peer] = hits + [now]
        while len(self.login_hits) > 1000:
            self.login_hits.popitem(last=False)
        guild_id = request.query.get("guild_id") or request.query.get("server")
        if guild_id:
            guild_id = str(snowflake(guild_id))
        self.states = OrderedDict((s, pending) for s, pending in self.states.items() if pending.expires > now)
        state = secrets.token_urlsafe(32)
        self.states[state] = OAuthState(now + 300, guild_id)
        while len(self.states) > 1000:
            self.states.popitem(last=False)
        params = {
            "client_id": self.config.client_id, "response_type": "code",
            "redirect_uri": self.config.callback_url, "scope": "identify guilds", "state": state,
        }
        if install:
            permissions = discord.Permissions(view_channel=True, send_messages=True, read_message_history=True,
                                              manage_messages=True, embed_links=True, moderate_members=True,
                                              send_messages_in_threads=True, add_reactions=True, manage_roles=True)
            params.update(scope="identify guilds bot applications.commands", permissions=str(permissions.value), integration_type="0")
        if guild_id:
            params["guild_id"] = guild_id
            if install:
                params["disable_guild_select"] = "true"
        response = web.HTTPFound("https://discord.com/oauth2/authorize?" + urlencode(params))
        self.cookie(response, STATE_COOKIE, state, 300)
        return response

    async def callback(self, request):
        state = request.query.get("state", "")
        pending = self.states.get(state)
        if (not state or not hmac.compare_digest(state, request.cookies.get(STATE_COOKIE, ""))
                or pending is None or pending.expires <= time.time()):
            raise web.HTTPBadRequest(reason="Sign-in expired or could not be verified. Please start again.")
        self.states.pop(state)
        if request.query.get("iss", "https://discord.com") != "https://discord.com":
            raise web.HTTPBadRequest(reason="Unexpected sign-in provider.")
        response = web.HTTPFound("/dashboard")
        response.del_cookie(STATE_COOKIE, path="/")
        if request.query.get("error") or not request.query.get("code"):
            response.headers["Location"] = "/?notice=cancelled"
            return response
        try:
            tokens = await self.api.exchange(request.query["code"], self.config)
            scopes = set(tokens.get("scope", "").split())
            if not {"identify", "guilds"} <= scopes or tokens.get("token_type", "").lower() != "bearer":
                raise ValueError("Missing sign-in scopes")
            user = await self.api.user(tokens["access_token"])
            user_id = str(snowflake(user["id"]))
            ttl = min(8 * 3600, int(tokens["expires_in"]))
            if ttl <= 0:
                raise ValueError("Expired token")
            session = Session(tokens["access_token"], {
                "id": user_id, "name": str(user.get("global_name") or user.get("username") or "Discord member")[:100],
            }, secrets.token_urlsafe(32), time.time() + ttl)
        except (web.HTTPException, ValueError, KeyError):
            LOG.warning("Discord website sign-in failed during token exchange or scope verification; credentials were not logged.")
            response.headers["Location"] = "/?notice=signin-failed"
            return response
        self.sessions = OrderedDict((key, value) for key, value in self.sessions.items() if value.expires > time.time())
        self.sessions.pop(request.cookies.get(COOKIE, ""), None)  # Rotate sessions at sign-in.
        key = secrets.token_urlsafe(48)
        self.sessions[key] = session
        while len(self.sessions) > 5000:
            self.sessions.popitem(last=False)
        self.cookie(response, COOKIE, key, ttl)
        # This is a navigation hint only. Every settings request rechecks access.
        guild_id = pending.guild_id or request.query.get("guild_id", "")
        if guild_id.isascii() and guild_id.isdecimal() and 0 < int(guild_id) < 2**64:
            response.headers["Location"] = "/dashboard?server=" + str(int(guild_id))
        LOG.info("Discord website sign-in succeeded: user=%s", user_id)
        return response

    async def logout(self, request):
        self.check_csrf(request, self.session(request))
        self.sessions.pop(request.cookies.get(COOKIE, ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie(COOKIE, path="/")
        return response

    async def session_info(self, request):
        session = self.session(request)
        return web.json_response({"user": session.user, "csrf": session.csrf})

    async def servers(self, request):
        session = self.session(request)
        guilds = await self.api.guilds(session.access_token)
        ready = bool(self.bot and self.bot.is_ready())
        return web.json_response({"bot_online": ready, "servers": [
            {"id": g["id"], "name": g["name"], "installed": bool(self.bot.get_guild(int(g["id"]))) if ready else None}
            for g in guilds if eligible(g)
        ]})

    async def authorize(self, request, session):
        guild_id = snowflake(request.match_info["guild_id"])
        # Fresh Discord response for each read and write; no cached login permission.
        guilds = await self.api.guilds(session.access_token)
        if not any(int(g["id"]) == guild_id and eligible(g) for g in guilds):
            raise web.HTTPForbidden(reason="You need Manage Server permission for this server.")
        if not self.bot or not self.bot.is_ready():
            raise web.HTTPServiceUnavailable(reason="Espresso is reconnecting. Please try again shortly.")
        guild = self.bot.get_guild(guild_id)
        if guild is None or guild.unavailable:
            raise web.HTTPNotFound(reason="Espresso is not available in this server. Add it first.")
        return guild

    async def get_settings(self, request):
        guild = await self.authorize(request, self.session(request))
        try:
            roles = await guild.fetch_roles()
            channels = await guild.fetch_channels()
        except discord.HTTPException:
            raise web.HTTPServiceUnavailable(reason="Could not read server roles and channels. Please try again.") from None
        settings, version = await self.store.snapshot(guild.id)
        return web.json_response({
            "settings": settings_json(settings), "version": version,
            "roles": [{"id": str(r.id), "name": r.name} for r in roles if r.id != guild.id],
            "channels": [{"id": str(c.id), "name": c.name,
                          "can_log": bool(guild.me and all(getattr(c.permissions_for(guild.me), p) for p in ("view_channel", "send_messages", "embed_links")))}
                         for c in channels if isinstance(c, discord.TextChannel)],
        })

    async def save_settings(self, request):
        session = self.session(request)
        self.check_csrf(request, session)
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(reason="Send settings as JSON.")
        guild = await self.authorize(request, session)
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {"version", "changes"}:
            raise ValueError("Invalid settings request")
        if type(data["version"]) is not int or data["version"] < 0 or not isinstance(data["changes"], dict):
            raise ValueError("Invalid version")
        changes = dict(data["changes"])
        if not changes or not changes.keys() <= asdict(Settings()).keys():
            raise ValueError("Unknown or empty settings")
        if "bad_words" in changes:
            if not isinstance(changes["bad_words"], list) or any(type(w) is not str for w in changes["bad_words"]):
                raise ValueError("Invalid phrase list")
            changes["bad_words"] = list(dict.fromkeys(normalize(w).strip() for w in changes["bad_words"]))
        if "exempt_roles" in changes:
            if not isinstance(changes["exempt_roles"], list):
                raise ValueError("Invalid role list")
            changes["exempt_roles"] = list(dict.fromkeys(snowflake(r) for r in changes["exempt_roles"]))
        if "log_channel" in changes and changes["log_channel"] is not None:
            changes["log_channel"] = snowflake(changes["log_channel"])
        if "hate_filters" in changes:
            validate_filters(changes["hate_filters"])
            for config in changes["hate_filters"].values():
                for key in ("extra_terms", "allowed_terms"):
                    config[key] = list(dict.fromkeys(normalize(term).strip() for term in config[key]))
        if "reaction_roles" in changes:
            if type(changes["reaction_roles"]) is not list or any(type(mapping) is not dict for mapping in changes["reaction_roles"]):
                raise ValueError("Invalid reaction role list")
            changes["reaction_roles"] = [{**mapping, **{key:snowflake(mapping.get(key)) for key in ("channel_id", "message_id", "role_id")}}
                                         for mapping in changes["reaction_roles"]]
        # Validate types/ranges before making any Discord or database mutations.
        current, _ = await self.store.snapshot(guild.id)
        Settings(**(asdict(current) | changes))
        reaction_changed = (changes.get("reaction_roles", current.reaction_roles) != current.reaction_roles
                            or changes.get("reaction_roles_enabled", current.reaction_roles_enabled) != current.reaction_roles_enabled)
        prepared = []
        try:
            if "exempt_roles" in changes:
                role_ids = {r.id for r in await guild.fetch_roles() if r.id != guild.id}
                if not set(changes["exempt_roles"]) <= role_ids:
                    raise ValueError("An exempt role does not belong to this server")
            if changes.get("log_channel") is not None:
                channel = next((c for c in await guild.fetch_channels() if c.id == changes["log_channel"]), None)
                if not isinstance(channel, discord.TextChannel) or guild.me is None:
                    raise ValueError("Invalid log channel")
                perms = channel.permissions_for(guild.me)
                if not (perms.view_channel and perms.send_messages and perms.embed_links):
                    raise web.HTTPBadRequest(reason="Espresso needs View Channel, Send Messages, and Embed Links in that log channel.")
            if reaction_changed:
                actor = await guild.fetch_member(int(session.user["id"]))
                try:
                    prepared = await self.bot.reaction_manager.validate(guild, actor, changes.get("reaction_roles", current.reaction_roles))
                except ValueError as exc:
                    raise web.HTTPBadRequest(reason=str(exc)) from None
        except discord.HTTPException:
            raise web.HTTPServiceUnavailable(reason="Could not verify this server's roles or channels. Please retry.") from None
        try:
            settings, version = await self.store.update_from_web(guild.id, data["version"], int(session.user["id"]), changes)
        except SettingsConflict as exc:
            raise web.HTTPConflict(reason=str(exc)) from None
        self.bot.spam.reset_guild(guild.id)
        setup_warnings = []
        if reaction_changed and settings.reaction_roles_enabled:
            setup_warnings = await self.bot.reaction_manager.seed(prepared)
        LOG.info("Settings saved: guild=%s actor=%s fields=%s", guild.id, session.user["id"], ",".join(sorted(changes)))
        # Audit is committed with settings. Discord channel logging is best effort.
        try:
            await self.bot.log_action(guild, "Web settings changed", f"Moderator {session.user['id']} updated {', '.join(sorted(changes))}.")
        except Exception as exc:
            LOG.warning("Settings saved but channel log failed (%s).", type(exc).__name__)
        return web.json_response({"settings": settings_json(settings), "version": version, "setup_warnings": setup_warnings})
