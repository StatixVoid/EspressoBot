"""Authenticated community dashboard routes using the panel's existing session and CSRF checks."""

import discord
from aiohttp import web

from .community import manager, staff, readable_ids
from .store import SettingsConflict


def decode_extensions(changes, snowflake):
    for name, keys in (("welcome",("channel_id",)),("community",("prompt_channel_id","notification_role_id"))):
        if name in changes:
            if type(changes[name]) is not dict:
                raise ValueError("Invalid community settings.")
            changes[name] = dict(changes[name])
            for key in keys:
                if changes[name].get(key) is not None:
                    changes[name][key] = snowflake(changes[name][key])
    if "channel_rules" in changes:
        if type(changes["channel_rules"]) is not list or any(type(row) is not dict for row in changes["channel_rules"]):
            raise ValueError("Invalid channel rules.")
        changes["channel_rules"] = [{**row,"channel_id":snowflake(row.get("channel_id"))} for row in changes["channel_rules"]]


def install_community_routes(panel):
    from .web import snowflake

    async def context(request, *, write=False, moderation=False):
        session = panel.session(request)
        if write:
            panel.check_csrf(request,session)
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType(reason="Send this action as JSON.")
        guild = await panel.authorize(request,session)
        try:
            actor = await guild.fetch_member(int(session.user["id"]))
            (staff if moderation else manager)(actor)
        except discord.HTTPException:
            raise web.HTTPServiceUnavailable(reason="Could not verify your current server permissions.") from None
        except ValueError as exc:
            raise web.HTTPForbidden(reason=str(exc)) from None
        return guild,actor,panel.bot.community_manager

    async def data(request, required, optional=()):
        value = await request.json()
        if type(value) is not dict or not set(required) <= value.keys() or not value.keys() <= set(required)|set(optional):
            raise ValueError("Invalid action fields.")
        return value

    async def guarded(fn, request):
        try:
            return await fn(request)
        except SettingsConflict as exc:
            raise web.HTTPConflict(reason=str(exc)) from None
        except ValueError as exc:
            raise web.HTTPBadRequest(reason=str(exc)) from None
        except discord.HTTPException:
            raise web.HTTPServiceUnavailable(reason="Discord could not confirm the action. Refresh and check its status before retrying.") from None

    def route(method, path, fn):
        async def handle(request):
            return await guarded(fn,request)
        return method("/api/servers/{guild_id}/"+path,handle)

    def page(rows, key):
        return {key:[readable_ids(row) for row in rows[:50]],"next_cursor":str(rows[49]["id"]) if len(rows)>50 else None}

    async def inbox(request):
        guild,actor,features = await context(request,moderation=True)
        status = request.query.get("status","open")
        if status not in ("open","closed","all"):
            raise ValueError("Invalid inbox status.")
        before = snowflake(request.query["before"]) if "before" in request.query else None
        return web.json_response(page(await features.store.inbox(guild.id,before,status),"items"))

    async def review(request):
        guild,actor,features = await context(request,write=True,moderation=True)
        value = await data(request,("version","action","note"))
        status = await features.review(guild,actor,snowflake(request.match_info["item_id"]),value["version"],value["action"],value["note"])
        return web.json_response({"status":status})

    async def history(request):
        guild,actor,features = await context(request,moderation=True)
        rows = await features.store.inbox_history(guild.id,snowflake(request.match_info["item_id"]))
        return web.json_response({"history":[readable_ids(row) for row in rows]})

    async def cases(request):
        guild,actor,features = await context(request,moderation=True)
        before = snowflake(request.query["before"]) if "before" in request.query else None
        user_id = snowflake(request.query["user_id"]) if request.query.get("user_id") else None
        return web.json_response(page(await features.store.cases(guild.id,before,user_id),"cases"))

    async def remove_warning(request):
        guild,actor,features = await context(request,write=True,moderation=True)
        value = await data(request,("reason",))
        changed = await features.remove_warning(guild,actor,snowflake(request.match_info["warning_id"]),value["reason"])
        return web.json_response({"changed":changed})

    async def test_filter(request):
        guild,actor,features = await context(request,write=True)
        value = await data(request,("text",),("channel_id",))
        channel_id,parent_id = None,None
        if value.get("channel_id"):
            channel_id = snowflake(value["channel_id"])
            channel = await guild.fetch_channel(channel_id)
            if not channel.permissions_for(actor).view_channel:
                raise web.HTTPForbidden(reason="Choose a channel you can view.")
            parent_id = getattr(channel,"parent_id",None)
        return web.json_response(await features.test_filter(guild.id,value["text"],channel_id,parent_id))

    async def health(request):
        guild,actor,features = await context(request)
        recoveries = await features.store.recovery(guild.id)
        return web.json_response({"checks":await features.diagnostics(guild),"protection":[{"channel_id":str(row["channel_id"]),"restore_at":row["restore_at"],"action":row["data"]["action"],"error":row["data"].get("error","")} for row in recoveries]})

    async def end_protection(request):
        guild,actor,features = await context(request,write=True)
        await data(request,())
        return web.json_response(await features.end_protection(guild,actor))

    async def posts(request):
        guild,actor,features = await context(request)
        return web.json_response({"posts":[readable_ids(row) for row in await features.store.posts(guild.id)]})

    async def schedule(request):
        guild,actor,features = await context(request,write=True)
        value = await data(request,("channel_id","content","due_at","repeat_days","notify","request_id"))
        request_id = value["request_id"]
        if type(request_id) is not str or not 8 <= len(request_id) <= 100 or not all(c.isascii() and (c.isalnum() or c == "-") for c in request_id):
            raise ValueError("Invalid scheduling request ID.")
        post_id = await features.schedule(guild,actor,snowflake(value["channel_id"]),value["content"],value["due_at"],value["repeat_days"],value["notify"],"web:"+request_id)
        return web.json_response({"id":str(post_id)},status=201)

    async def cancel(request):
        guild,actor,features = await context(request,write=True)
        await data(request,())
        post_id = snowflake(request.match_info["post_id"])
        await features.store.cancel_post(guild.id,post_id)
        await panel.bot.log_action(guild,"Scheduled post cancelled",f"Post #{post_id} · moderator {actor.id}.")
        return web.json_response({"ok":True})

    panel.app.add_routes([
        route(web.get,"inbox",inbox),route(web.post,"inbox/{item_id}",review),
        route(web.get,"inbox/{item_id}/history",history),route(web.get,"cases",cases),
        route(web.post,"warnings/{warning_id}/remove",remove_warning),
        route(web.post,"test-filter",test_filter),route(web.get,"health",health),
        route(web.post,"protection/end",end_protection),route(web.get,"posts",posts),
        route(web.post,"posts",schedule),route(web.post,"posts/{post_id}/cancel",cancel),
    ])
