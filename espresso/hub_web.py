"""Manager-only community hub configuration and operations behind OAuth and CSRF."""

import discord
from aiohttp import web

from .hub_config import identifier
from .store import SettingsConflict


def wire(value):
    if type(value) is list:
        return [wire(item) for item in value]
    if type(value) is dict:
        return {key:([str(item) for item in item_value] if key.endswith("_ids") else str(item_value) if item_value is not None and (key=="id" or key.endswith("_id")) else wire(item_value)) for key,item_value in value.items()}
    return value


def install_hub_routes(panel):
    async def context(request, write=False):
        session = panel.session(request)
        if write:
            panel.check_csrf(request,session)
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType(reason="Send community actions as JSON.")
        guild = await panel.authorize(request,session)
        hub = panel.bot.hub_manager
        try:
            actor = await hub.manager(guild,int(session.user["id"]))
        except ValueError as exc:
            raise web.HTTPForbidden(reason=str(exc)) from None
        return guild,actor,hub

    async def body(request, required, optional=()):
        value = await request.json()
        if type(value) is not dict or not set(required)<=value.keys() or not value.keys()<=set(required)|set(optional):
            raise ValueError("Invalid community action fields.")
        return value

    def route(method,path,handler):
        async def handle(request):
            try:
                return await handler(request)
            except SettingsConflict as exc:
                raise web.HTTPConflict(reason=str(exc)) from None
            except ValueError as exc:
                raise web.HTTPBadRequest(reason=str(exc)) from None
            except discord.HTTPException:
                raise web.HTTPServiceUnavailable(reason="Discord could not confirm this action. Refresh the hub and check Discord before retrying.") from None
        return method("/api/servers/{guild_id}/hub"+path,handle)

    async def get_hub(request):
        guild,actor,hub = await context(request)
        policy,version = await panel.store.hub_settings(guild.id)
        channels = await guild.fetch_channels()
        visible = [c for c in channels if c.permissions_for(actor).view_channel]
        roles = await guild.fetch_roles()
        return web.json_response(wire({"settings":policy,"version":version,
            "channels":[{"id":c.id,"name":c.name,"kind":"text" if isinstance(c,discord.TextChannel) else "voice" if isinstance(c,discord.VoiceChannel) else "category"} for c in visible if isinstance(c,(discord.TextChannel,discord.VoiceChannel,discord.CategoryChannel))],
            "roles":[{"id":r.id,"name":r.name} for r in roles if not r.is_default() and not r.managed],
            "faq":await panel.store.faq_entries(guild.id),"issues":await panel.store.hub_deliveries(guild.id)}))

    async def save_hub(request):
        guild,actor,hub = await context(request,True)
        value = await body(request,("settings","version"))
        policy = await hub.validate_settings(guild,actor.id,value["settings"])
        version = await panel.store.save_hub_settings(guild.id,actor.id,value["version"],policy)
        await panel.bot.log_action(guild,"Community hub settings changed",f"Manager {actor.id} · hub settings version {version}.")
        return web.json_response({"version":version,"settings":wire(policy)})

    async def list_items(request):
        guild,actor,hub = await context(request)
        kind = request.match_info["kind"]
        if kind not in ("ticket","event","suggestion","voice","highlight"):
            raise ValueError("Unknown community list.")
        before = identifier(request.query["before"]) if "before" in request.query else None
        rows = await panel.store.hub_items(guild.id,kind,before=before)
        output = []
        for row in rows[:50]:
            row["data"] = {key:value for key,value in row["data"].items() if key not in ("transcript","transcript_until")}
            row.pop("request_hash",None)
            if kind=="suggestion":
                row["votes"] = await panel.store.suggestion_votes(guild.id,row["id"])
            if kind=="event":
                row["subscribers"] = len(await panel.store.rsvps(guild.id,row["id"]))
            output.append(row)
        return web.json_response({"items":wire(output),"next_cursor":str(rows[49]["id"]) if len(rows)>50 else None})

    async def action(request):
        guild,actor,hub = await context(request,True)
        value = await body(request,("action","version"),("reason","status","checked_discord"))
        item_id = identifier(request.match_info["item_id"])
        row = await panel.store.hub_item(guild.id,item_id)
        if row is None:
            raise ValueError("This community item is not in the server.")
        if type(value["version"]) is not int or value["version"]!=row["version"]:
            raise SettingsConflict("This item changed. Refresh before trying again.")
        name = value["action"]
        if name=="claim" and row["kind"]=="ticket":
            await hub.claim_ticket(guild,actor.id,item_id,value["version"])
        elif name=="close" and row["kind"]=="ticket":
            await hub.close_ticket(guild,actor.id,item_id,value.get("reason"),value["version"])
        elif name=="status" and row["kind"]=="suggestion":
            await hub.suggestion_status(guild,actor.id,item_id,value.get("status"),value.get("reason"),value["version"])
        elif name=="end" and row["kind"]=="event":
            await hub.end_event(guild,actor.id,item_id,value["version"])
        elif name=="remove" and row["kind"]=="highlight":
            await hub.remove_highlight(guild,actor.id,item_id)
        elif name=="retire" and value.get("checked_discord") is True:
            await hub.retire_request(guild,actor.id,item_id,value["version"])
        else:
            raise ValueError("This action is not available for that item.")
        return web.json_response({"message":"Community item updated. Refresh to see its current state."})

    async def create_event(request):
        guild,actor,hub = await context(request,True)
        value = await body(request,("name","description","location","start_at","end_at","request_id"))
        row = await hub.create_event(guild,actor.id,value["name"],value["description"],value["location"],value["start_at"],value["end_at"],value["request_id"])
        return web.json_response(wire({"id":row["id"],"status":row["status"]}),status=201)

    async def faq(request):
        guild,actor,hub = await context(request,True)
        value = await body(request,("answer","summary","version"))
        await panel.store.save_faq(guild.id,actor.id,request.match_info["name"],value["answer"],value["summary"],value["version"],request.method=="DELETE")
        return web.json_response({"message":"Saved reply removed." if request.method=="DELETE" else "Saved reply updated."})

    async def badge(request):
        guild,actor,hub = await context(request,True)
        value = await body(request,("member_id","name","remove"))
        if type(value["remove"]) is not bool:
            raise ValueError("Choose whether to remove the badge.")
        await hub.policy(guild.id,"profiles")
        member = await hub.member(guild,identifier(value["member_id"]))
        await panel.store.badge(guild.id,member.id,value["name"],actor.id,value["remove"])
        return web.json_response({"message":"Badge removed." if value["remove"] else "Badge awarded."})

    panel.app.add_routes([route(web.get,"",get_hub),route(web.patch,"",save_hub),route(web.get,"/items/{kind}",list_items),
        route(web.post,"/items/{item_id}/action",action),route(web.post,"/events",create_event),
        route(web.put,"/faq/{name}",faq),route(web.delete,"/faq/{name}",faq),route(web.post,"/badges",badge)])
