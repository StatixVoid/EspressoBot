"""Fresh Discord staff authorization precedes every premium access check."""

from aiohttp import web


def install_premium_routes(panel):
    async def access(request):
        session = panel.session(request)
        guild = await panel.authorize(request, session, permission="dashboard")
        return web.json_response(panel.premium.access(guild.id, int(session.user["id"])))

    async def insights(request):
        session = panel.session(request)
        guild = await panel.authorize(request, session, permission="dashboard")
        if not panel.premium.access(guild.id, int(session.user["id"]))["active"]:
            raise web.HTTPForbidden(reason="Premium Insights is available to allowlisted pilot servers and staff accounts.")
        value = request.query.get("days", "7")
        if value not in {"7", "30"}:
            raise ValueError("Choose a 7- or 30-day report.")
        return web.json_response(await panel.store.moderation_insights(guild.id, int(value)))

    panel.app.add_routes([
        web.get("/api/servers/{guild_id}/premium", access),
        web.get("/api/servers/{guild_id}/premium/insights", insights),
    ])
