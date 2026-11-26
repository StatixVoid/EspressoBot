"""Aggregate existing moderation cases without exposing member or case content."""

from datetime import datetime, timedelta, timezone
import time


class PremiumStore:
    async def moderation_insights(self, guild_id, days=7, *, now=None):
        if type(days) is not int or days not in (7, 30):
            raise ValueError("Choose a 7- or 30-day report.")
        now = time.time() if now is None else now
        today = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=days-1)
        bounds = (guild_id, start.timestamp(), now)
        async with self.lock:
            async with self.db.execute("""
                SELECT COUNT(*) AS cases, COUNT(DISTINCT user_id) AS members,
                    COALESCE(SUM(kind='warning'),0) AS warnings,
                    COALESCE(SUM(kind='timeout'),0) AS timeouts
                FROM moderation_cases WHERE guild_id=? AND created_at>=? AND created_at<=?
            """, bounds) as cursor:
                totals = dict(await cursor.fetchone())
            async with self.db.execute("""
                SELECT strftime('%Y-%m-%d',created_at,'unixepoch') AS day, COUNT(*) AS cases
                FROM moderation_cases WHERE guild_id=? AND created_at>=? AND created_at<=?
                GROUP BY day ORDER BY day
            """, bounds) as cursor:
                counts = {row["day"]: row["cases"] for row in await cursor.fetchall()}
        return {"guild_id": str(guild_id), "days": days, "timezone": "UTC",
                "starts_at": start.timestamp(), "generated_at": now, "totals": totals,
                "daily": [{"date": (start+timedelta(days=i)).date().isoformat(),
                           "cases": counts.get((start+timedelta(days=i)).date().isoformat(), 0)}
                          for i in range(days)]}
