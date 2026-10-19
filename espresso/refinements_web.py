"""Permission-scoped overview, history, simulation, calendar and role-menu APIs."""

from dataclasses import asdict
import copy
import json
import math
import time

import discord
from aiohttp import web

from .community import manager, staff, readable_ids
from .rules import Settings
from .scheduling import calendar_policy, local_timestamp, next_occurrence, following_due, policy_from_row
from .simulation import simulate
from .store import SettingsConflict


def proposed_settings(raw):
    from .web import snowflake
    from .community_web import decode_extensions
    if type(raw) is not dict or not raw.keys() <= asdict(Settings()).keys():
        raise ValueError("Invalid proposed settings.")
    value = copy.deepcopy(raw)
    if "exempt_roles" in value:
        value["exempt_roles"] = [snowflake(role) for role in value["exempt_roles"]]
    if value.get("log_channel") is not None:
        value["log_channel"] = snowflake(value["log_channel"])
    for mapping in value.get("reaction_roles",[]):
        for key in ("channel_id","message_id","role_id"):
            mapping[key] = snowflake(mapping[key])
    for key in ("verified_role_id","entry_channel_id"):
        if value.get("screening",{}).get(key) is not None:
            value["screening"][key] = snowflake(value["screening"][key])
    decode_extensions(value,snowflake)
    return value


async def simulation_payload(panel, guild, value):
    from .web import snowflake
    settings = await panel.store.settings(guild.id)
    if "settings" in value:
        settings = Settings(**(asdict(settings)|proposed_settings(value["settings"])))
    if value.get("context") is not None and type(value["context"]) is not dict:
        raise ValueError("Simulation context must be an object.")
    context = dict(value.get("context") or {})
    if "role_ids" in context:
        context["role_ids"] = [snowflake(role) for role in context["role_ids"]]
    channel_id,parent_id,timeout_error = None,None,None
    if value.get("channel_id"):
        channel_id = snowflake(value["channel_id"])
        channel = await guild.fetch_channel(channel_id)
        parent_id = getattr(channel,"parent_id",None)
    if value.get("member_id"):
        from .bot import target_error
        member = await guild.fetch_member(snowflake(value["member_id"]))
        context.update(role_ids=[role.id for role in member.roles],administrator=member.guild_permissions.administrator,
                       owner=member.id == guild.owner_id,bot=member.bot)
        timeout_error = target_error(None,member,guild.me)
        if not timeout_error and (guild.me is None or not guild.me.guild_permissions.moderate_members):
            timeout_error = "Espresso needs Moderate Members."
    return simulate(value.get("text"),settings,context,channel_id=channel_id,parent_id=parent_id,timeout_error=timeout_error)


def prepare_schedule(value, *, now=None):
    now = time.time() if now is None else now
    calendar = calendar_policy(value.get("calendar"))
    due = value.get("due_at")
    if value.get("local_start"):
        due = local_timestamp(value["local_start"],calendar["timezone"])
    if type(due) not in (int,float) or not math.isfinite(due):
        raise ValueError("Choose a valid delivery date and time.")
    if calendar["recurrence"] in ("daily","weekly"):
        due = next_occurrence(max(now+9,due-1),calendar)
    if not now+10 <= due <= now+366*86400:
        raise ValueError("Schedule between 10 seconds and one year from now.")
    return due,calendar


def install_refinement_routes(panel):
    from .web import snowflake, settings_json

    async def context(request, *, write=False, permission="manage_guild"):
        session = panel.session(request)
        if write:
            panel.check_csrf(request,session)
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType(reason="Send this action as JSON.")
        guild = await panel.authorize(request,session,permission=permission)
        actor = await guild.fetch_member(int(session.user["id"]))
        try:
            if permission == "manage_guild":
                manager(actor)
            elif permission == "moderate_members":
                staff(actor)
            elif not actor.guild_permissions.manage_guild and not actor.guild_permissions.moderate_members:
                raise ValueError("Dashboard access requires Manage Server or Moderate Members.")
        except ValueError as exc:
            raise web.HTTPForbidden(reason=str(exc)) from None
        return guild,actor

    async def body(request, required=(), optional=()):
        data = await request.json()
        if type(data) is not dict or not set(required) <= data.keys() or not data.keys() <= set(required)|set(optional):
            raise ValueError("Invalid action fields.")
        return data

    def route(method, path, handler):
        async def guarded(request):
            try:
                return await handler(request)
            except SettingsConflict as exc:
                raise web.HTTPConflict(reason=str(exc)) from None
            except ValueError as exc:
                raise web.HTTPBadRequest(reason=str(exc)) from None
            except discord.HTTPException:
                raise web.HTTPServiceUnavailable(reason="Discord could not confirm this action. Refresh and check before retrying.") from None
        return method("/api/servers/{guild_id}/"+path,guarded)

    async def overview(request):
        guild,actor = await context(request,permission="dashboard")
        can_manage,can_moderate = actor.guild_permissions.manage_guild,actor.guild_permissions.moderate_members
        result = await panel.store.overview(guild.id,manager=can_manage,moderator=can_moderate)
        for key in ("waiting","post_issues"):
            result[key] = [readable_ids(row) for row in result.get(key,[])]
        if can_manage:
            try:
                result["health_issues"] = [check for check in await panel.bot.community_manager.diagnostics(guild) if not check["ok"]]
            except discord.HTTPException:
                result["health_issues"] = [{"name":"Server checks unavailable","ok":False,"detail":"Retry checks when Discord is available."}]
            result["protection"] = [{"channel_id":str(row["channel_id"]),"restore_at":row["restore_at"],"action":row["data"]["action"],"error":row["data"].get("error","")} for row in await panel.store.recovery(guild.id)]
        result["access"] = {"manage":can_manage,"moderate":can_moderate}
        return web.json_response(result)

    async def history(request):
        guild,actor = await context(request)
        before = int(request.query["before"]) if "before" in request.query else None
        rows = await panel.store.settings_history(guild.id,before)
        return web.json_response({"versions":[readable_ids(row) for row in rows[:50]],"next_cursor":rows[49]["version"] if len(rows)>50 else None})

    async def historical(request):
        guild,actor = await context(request)
        target = await panel.store.historical_settings(guild.id,int(request.match_info["version"]))
        current,version = await panel.store.snapshot(guild.id)
        old,new = settings_json(current),settings_json(target)
        changes = {key:value for key,value in new.items() if value != old[key]}
        return web.json_response({"version":version,"target_version":int(request.match_info["version"]),"changes":changes,"diff":[{"field":key,"before":old[key],"after":value} for key,value in changes.items()]})

    async def post_action(request):
        guild,actor = await context(request,write=True)
        data = await body(request,("version","action"),("changes","acknowledge"))
        post_id = snowflake(request.match_info["post_id"])
        row = await panel.store.post(guild.id,post_id)
        if row is None:
            raise ValueError("That post is not in this server.")
        changes = data.get("changes",{})
        fields = {}
        if changes or data["action"] in ("resume","retry"):
            if type(changes) is not dict or not changes.keys() <= {"channel_id","content","due_at","repeat_days","notify","calendar","local_start"}:
                raise ValueError("Invalid scheduled-post edits.")
            merged = {**row,"calendar":policy_from_row(row),**changes}
            if "notify" in changes and type(changes["notify"]) is not bool:
                raise ValueError("The notification switch must be a boolean.")
            due,calendar = prepare_schedule(merged)
            channel_id = snowflake(changes["channel_id"]) if "channel_id" in changes else row["channel_id"]
            channel = await panel.bot.community_manager.post_channel(guild,channel_id)
            if not channel.permissions_for(actor).view_channel or not channel.permissions_for(actor).send_messages:
                raise web.HTTPForbidden(reason="You cannot post in that channel.")
            if type(merged["content"]) is not str or not 1 <= len(merged["content"].strip()) <= 1800 or type(merged["repeat_days"]) is not int or not 0 <= merged["repeat_days"] <= 365 or type(merged["notify"]) not in (bool,int) or merged["notify"] not in (True,False,0,1):
                raise ValueError("Check the message, repeat interval, and notification switch.")
            settings = await panel.store.settings(guild.id)
            if merged["notify"] and settings.community["notification_role_id"] is None:
                raise ValueError("Configure a notification role before enabling notifications.")
            fields = {"channel_id":channel_id,"content":merged["content"].strip(),"due_at":due,"repeat_days":merged["repeat_days"],"notify":bool(merged["notify"]),
                      **calendar,"weekdays":json.dumps(calendar["weekdays"])}
        status = await panel.store.change_post(guild.id,post_id,data["version"],data["action"],actor.id,fields,data.get("acknowledge",False))
        await panel.bot.log_action(guild,"Scheduled post updated",f"Post #{post_id} · moderator {actor.id} · action {data['action']} · {status}.")
        return web.json_response({"status":status})

    async def calendar(request):
        guild,actor = await context(request)
        start,end = float(request.query.get("start",time.time())),float(request.query.get("end",time.time()+42*86400))
        if not math.isfinite(start) or not math.isfinite(end) or not 0 < end-start <= 62*86400:
            raise ValueError("Choose a calendar range of up to 62 days.")
        events = []
        for row in await panel.store.posts(guild.id):
            if row["status"] not in ("pending","paused"):
                continue
            projected = dict(row)
            if projected["due_at"] < start:
                projected["due_at"] = following_due(projected,start-1) or projected["due_at"]
            for _ in range(63):
                due = projected["due_at"]
                if due >= end:
                    break
                if due >= start:
                    events.append({"id":str(row["id"]),"due_at":due,"timezone":row["timezone"],"status":row["status"],"content":row["content"][:80]})
                next_due = following_due(projected,due)
                if next_due is None:
                    break
                projected["due_at"] = next_due
                if len(events) >= 1000:
                    break
            if len(events) >= 1000:
                break
        return web.json_response({"events":sorted(events,key=lambda row:row["due_at"]),"limit_reached":len(events)>=1000})

    async def protection(request):
        guild,actor = await context(request,write=True)
        data = await body(request,("action",),("minutes",))
        channel_id = snowflake(request.match_info["channel_id"])
        features = panel.bot.community_manager
        if data["action"] == "extend":
            await features.extend_protection(guild,actor,channel_id,data.get("minutes",10))
            return web.json_response({"ok":True})
        if data["action"] == "end":
            return web.json_response({"restored":await features.end_channel_protection(guild,actor,channel_id)})
        raise ValueError("Choose extend or end protection.")

    async def menus(request):
        guild,actor = await context(request)
        return web.json_response({"menus":[readable_ids({**row,"data":{**row["data"],"options":[{**option,"role_id":str(option["role_id"])} for option in row["data"]["options"]]}}) for row in await panel.store.menus(guild.id)]})

    async def publish_menu(request):
        guild,actor = await context(request,write=True)
        data = await body(request,("channel_id","menu","request_id"))
        if type(data["request_id"]) is not str or not 8 <= len(data["request_id"]) <= 100 or not all(c.isascii() and (c.isalnum() or c == "-") for c in data["request_id"]):
            raise ValueError("Invalid role-menu request ID.")
        menu = copy.deepcopy(data["menu"])
        if type(menu) is not dict or type(menu.get("options")) is not list:
            raise ValueError("Invalid menu choices.")
        for option in menu["options"]:
            if type(option) is not dict:
                raise ValueError("Invalid menu choice.")
            option["role_id"] = snowflake(option.get("role_id"))
        row = await panel.bot.role_menu_manager.publish(guild,actor,snowflake(data["channel_id"]),menu,"web:"+data["request_id"])
        return web.json_response({"id":str(row["id"]),"status":row["status"],"message_id":str(row["message_id"]) if row["message_id"] else None})

    async def disable_menu(request):
        guild,actor = await context(request,write=True)
        await body(request)
        text = await panel.bot.role_menu_manager.disable(guild,actor,snowflake(request.match_info["menu_id"]))
        return web.json_response({"message":text})

    panel.app.add_routes([
        route(web.get,"overview",overview),route(web.get,"settings-history",history),route(web.get,"settings-history/{version}",historical),
        route(web.post,"posts/{post_id}/action",post_action),route(web.get,"calendar",calendar),route(web.post,"protection/{channel_id}",protection),
        route(web.get,"role-menus",menus),route(web.post,"role-menus",publish_menu),route(web.post,"role-menus/{menu_id}/disable",disable_menu),
    ])
