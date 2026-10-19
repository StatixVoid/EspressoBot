"""Server-scoped community hub records. Transactions share Store.lock."""

import calendar
from datetime import date
import hashlib
import json
import re
import time

from .hub_config import default_hub, validate_hub, text, SUGGESTION_STATUSES, THEMES

HUB_TABLES = ("hub_settings", "hub_items", "hub_votes", "hub_rsvps", "hub_birthdays", "hub_profiles",
              "hub_thanks", "hub_thanks_limits", "hub_badges", "hub_preferences", "hub_faq", "hub_deliveries", "hub_role_grants")
KINDS = ("ticket", "event", "suggestion", "voice", "highlight")


def unpack(row):
    if row is None:
        return None
    value = dict(row)
    value["data"] = json.loads(value["data"])
    return value


class HubStore:
    async def open_hub(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS hub_settings (
                guild_id INTEGER PRIMARY KEY, data TEXT NOT NULL, version INTEGER NOT NULL, actor_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hub_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, kind TEXT NOT NULL,
                user_id INTEGER NOT NULL, channel_id INTEGER, message_id INTEGER, status TEXT NOT NULL,
                data TEXT NOT NULL, source TEXT NOT NULL, request_hash TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(guild_id,kind,source)
            );
            CREATE INDEX IF NOT EXISTS hub_item_guild ON hub_items(guild_id,kind,status,id);
            CREATE INDEX IF NOT EXISTS hub_member_activity ON hub_items(guild_id,kind,user_id,created_at);
            CREATE TABLE IF NOT EXISTS hub_votes (
                guild_id INTEGER NOT NULL, item_id INTEGER NOT NULL, user_id INTEGER NOT NULL, value INTEGER NOT NULL,
                PRIMARY KEY(guild_id,item_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS hub_rsvps (
                guild_id INTEGER NOT NULL, item_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                PRIMARY KEY(guild_id,item_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS hub_birthdays (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, month INTEGER NOT NULL, day INTEGER NOT NULL,
                PRIMARY KEY(guild_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS hub_profiles (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, bio TEXT NOT NULL, theme TEXT NOT NULL,
                PRIMARY KEY(guild_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS hub_thanks (
                guild_id INTEGER NOT NULL, sender_id INTEGER NOT NULL, recipient_id INTEGER NOT NULL,
                source TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(guild_id,source)
            );
            CREATE INDEX IF NOT EXISTS hub_thanks_sender ON hub_thanks(guild_id,sender_id,created_at);
            CREATE TABLE IF NOT EXISTS hub_thanks_limits (
                guild_id INTEGER NOT NULL, sender_id INTEGER NOT NULL, recipient_id INTEGER NOT NULL,
                source TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(guild_id,source)
            );
            CREATE TABLE IF NOT EXISTS hub_badges (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL, actor_id INTEGER NOT NULL,
                PRIMARY KEY(guild_id,user_id,name)
            );
            CREATE TABLE IF NOT EXISTS hub_preferences (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, highlight_optout INTEGER NOT NULL,
                PRIMARY KEY(guild_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS hub_faq (
                guild_id INTEGER NOT NULL, name TEXT NOT NULL, answer TEXT NOT NULL, summary TEXT NOT NULL,
                version INTEGER NOT NULL, actor_id INTEGER NOT NULL, PRIMARY KEY(guild_id,name)
            );
            CREATE TABLE IF NOT EXISTS hub_deliveries (
                guild_id INTEGER NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, outcome TEXT NOT NULL,
                created_at REAL NOT NULL, PRIMARY KEY(guild_id,source)
            );
            CREATE TABLE IF NOT EXISTS hub_role_grants (
                guild_id INTEGER NOT NULL, purpose TEXT NOT NULL, user_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
                expires_at REAL NOT NULL, status TEXT NOT NULL, PRIMARY KEY(guild_id,purpose,user_id,role_id)
            );
        """)
        await self.db.execute("UPDATE hub_items SET status='uncertain' WHERE status='posting'")
        await self.db.execute("UPDATE hub_deliveries SET status='uncertain',outcome='Interrupted; check Discord. No automatic resend.' WHERE status='sending'")
        # Pending role writes are reconciled against actual membership before cleanup.
        await self.db.commit()

    async def hub_settings(self, guild_id):
        async with self.lock:
            async with self.db.execute("SELECT data,version FROM hub_settings WHERE guild_id=?", (guild_id,)) as cursor:
                row = await cursor.fetchone()
            return (validate_hub(json.loads(row[0])), row[1]) if row else (default_hub(), 0)

    async def save_hub_settings(self, guild_id, actor_id, version, data):
        from .store import SettingsConflict
        value = validate_hub(data)
        if type(version) is not int or version < 0:
            raise ValueError("Invalid hub settings version.")
        async with self.lock:
            async with self.db.execute("SELECT version FROM hub_settings WHERE guild_id=?", (guild_id,)) as cursor:
                row = await cursor.fetchone()
            if (row[0] if row else 0) != version:
                raise SettingsConflict("Community hub settings changed. Refresh before saving.")
            await self.db.execute("INSERT OR REPLACE INTO hub_settings VALUES (?,?,?,?)", (guild_id, json.dumps(value), version+1, actor_id))
            await self.db.commit()
        return version + 1

    async def hub_item(self, guild_id, item_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM hub_items WHERE guild_id=? AND id=?", (guild_id, item_id)) as cursor:
                return unpack(await cursor.fetchone())

    async def hub_item_source(self, guild_id, kind, source):
        async with self.lock:
            async with self.db.execute("SELECT * FROM hub_items WHERE guild_id=? AND kind=? AND source=?", (guild_id,kind,source)) as cursor:
                return unpack(await cursor.fetchone())

    async def hub_items(self, guild_id=None, kind=None, *, active=False, user_id=None, before=None, limit=51):
        clauses, args = ["1=1"], []
        for key, value in (("guild_id", guild_id), ("kind", kind), ("user_id", user_id)):
            if value is not None:
                clauses.append(key + "=?"); args.append(value)
        if active:
            clauses.append("status NOT IN ('closed','completed','declined','removed','cancelled','failed')")
        if before is not None:
            clauses.append("id<?"); args.append(before)
        args.append(min(max(limit, 1), 1000))
        async with self.lock:
            async with self.db.execute("SELECT * FROM hub_items WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?", args) as cursor:
                return [unpack(row) for row in await cursor.fetchall()]

    async def begin_hub_item(self, guild_id, kind, user_id, data, source, channel_id=None):
        if kind not in KINDS:
            raise ValueError("Unknown community feature.")
        source = text(source, "Request ID", 1, 100)
        digest = hashlib.sha256(json.dumps([user_id, data, channel_id], sort_keys=True).encode()).hexdigest()
        now = time.time()
        async with self.lock:
            async with self.db.execute("SELECT * FROM hub_items WHERE guild_id=? AND kind=? AND source=?", (guild_id,kind,source)) as cursor:
                existing = await cursor.fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise ValueError("This request ID already belongs to different content.")
                return unpack(existing), False
            if kind in ("ticket", "voice"):
                async with self.db.execute("SELECT 1 FROM hub_items WHERE guild_id=? AND kind=? AND user_id=? AND status NOT IN ('closed','removed','failed')", (guild_id,kind,user_id)) as cursor:
                    if await cursor.fetchone():
                        raise ValueError(f"You already have a {kind}. Use its existing room or ask staff to check it.")
                window,maximum = (86400,5) if kind=="ticket" else (600,5)
                async with self.db.execute("SELECT COUNT(*) FROM hub_items WHERE guild_id=? AND kind=? AND user_id=? AND created_at>?", (guild_id,kind,user_id,now-window)) as cursor:
                    if (await cursor.fetchone())[0]>=maximum:
                        raise ValueError("You can open five tickets in 24 hours." if kind=="ticket" else "You can create five voice rooms in ten minutes. Rejoin an existing room or wait a little.")
            if kind == "suggestion":
                async with self.db.execute("SELECT COUNT(*) FROM hub_items WHERE guild_id=? AND kind='suggestion' AND user_id=? AND created_at>?", (guild_id,user_id,now-86400)) as cursor:
                    if (await cursor.fetchone())[0] >= 3:
                        raise ValueError("You can submit three suggestions in 24 hours.")
            cap = {"ticket":100,"voice":30,"event":100,"suggestion":250,"highlight":200}[kind]
            async with self.db.execute("SELECT COUNT(*) FROM hub_items WHERE guild_id=? AND kind=? AND status NOT IN ('closed','completed','declined','removed','cancelled','failed')", (guild_id,kind)) as cursor:
                if (await cursor.fetchone())[0] >= cap:
                    raise ValueError(f"This server has reached its active {kind} limit ({cap}).")
            cursor = await self.db.execute("INSERT INTO hub_items(guild_id,kind,user_id,channel_id,status,data,source,request_hash,created_at,updated_at) VALUES (?,?,?,?,'posting',?,?,?,?,?)", (guild_id,kind,user_id,channel_id,json.dumps(data),source,digest,now,now))
            item_id = cursor.lastrowid
            await self.db.commit()
            async with self.db.execute("SELECT * FROM hub_items WHERE id=?", (item_id,)) as cursor:
                return unpack(await cursor.fetchone()), True

    async def update_hub_item(self, guild_id, item_id, *, version=None, status=None, data=None, channel_id=None, message_id=None):
        from .store import SettingsConflict
        async with self.lock:
            async with self.db.execute("SELECT * FROM hub_items WHERE guild_id=? AND id=?", (guild_id,item_id)) as cursor:
                row = unpack(await cursor.fetchone())
            if not row:
                raise ValueError("That community item is not in this server.")
            if version is not None and (type(version) is not int or row["version"] != version):
                raise SettingsConflict("This item changed. Refresh before trying again.")
            row.update(status=status if status is not None else row["status"], data=row["data"] | (data or {}),
                       channel_id=channel_id if channel_id is not None else row["channel_id"],
                       message_id=message_id if message_id is not None else row["message_id"])
            await self.db.execute("UPDATE hub_items SET status=?,data=?,channel_id=?,message_id=?,version=version+1,updated_at=? WHERE guild_id=? AND id=?", (row["status"],json.dumps(row["data"]),row["channel_id"],row["message_id"],time.time(),guild_id,item_id))
            await self.db.commit()
        return row

    async def vote_suggestion(self, guild_id, item_id, user_id, value):
        if type(value) is not int or value not in (-1,1):
            raise ValueError("Choose an upvote or downvote.")
        async with self.lock:
            async with self.db.execute("SELECT status FROM hub_items WHERE guild_id=? AND id=? AND kind='suggestion'", (guild_id,item_id)) as cursor:
                row = await cursor.fetchone()
            if row is None or row[0] not in SUGGESTION_STATUSES[:4]:
                raise ValueError("Voting is closed for this suggestion.")
            async with self.db.execute("SELECT value FROM hub_votes WHERE guild_id=? AND item_id=? AND user_id=?", (guild_id,item_id,user_id)) as cursor:
                prior = await cursor.fetchone()
            await self.db.execute("DELETE FROM hub_votes WHERE guild_id=? AND item_id=? AND user_id=?", (guild_id,item_id,user_id))
            if prior is None or prior[0] != value:
                await self.db.execute("INSERT INTO hub_votes VALUES (?,?,?,?)", (guild_id,item_id,user_id,value))
            await self.db.commit()
        return await self.suggestion_votes(guild_id,item_id)

    async def suggestion_votes(self, guild_id, item_id):
        async with self.lock:
            async with self.db.execute("SELECT value,COUNT(*) FROM hub_votes WHERE guild_id=? AND item_id=? GROUP BY value", (guild_id,item_id)) as cursor:
                counts = dict(await cursor.fetchall())
        return {"up": counts.get(1,0), "down": counts.get(-1,0)}

    async def set_rsvp(self, guild_id, item_id, user_id, enabled):
        async with self.lock:
            if enabled:
                await self.db.execute("INSERT OR IGNORE INTO hub_rsvps VALUES (?,?,?)", (guild_id,item_id,user_id))
            else:
                await self.db.execute("DELETE FROM hub_rsvps WHERE guild_id=? AND item_id=? AND user_id=?", (guild_id,item_id,user_id))
            await self.db.commit()

    async def rsvps(self, guild_id, item_id):
        async with self.lock:
            async with self.db.execute("SELECT user_id FROM hub_rsvps WHERE guild_id=? AND item_id=? LIMIT 1000", (guild_id,item_id)) as cursor:
                return [row[0] for row in await cursor.fetchall()]

    async def birthday(self, guild_id, user_id, month=None, day=None):
        if month is not None:
            if type(month) is not int or type(day) is not int:
                raise ValueError("Enter a month and day; no birth year is stored.")
            try:
                date(2000,month,day)
            except ValueError:
                raise ValueError("Enter a valid birthday month and day.") from None
        async with self.lock:
            if month is None:
                await self.db.execute("DELETE FROM hub_birthdays WHERE guild_id=? AND user_id=?", (guild_id,user_id))
            else:
                await self.db.execute("INSERT OR REPLACE INTO hub_birthdays VALUES (?,?,?,?)", (guild_id,user_id,month,day))
            await self.db.commit()

    async def birthdays_on(self, guild_id, today):
        async with self.lock:
            async with self.db.execute("SELECT user_id,month,day FROM hub_birthdays WHERE guild_id=? AND ((month=? AND day=?) OR (?=2 AND ?=28 AND ?=0 AND month=2 AND day=29))", (guild_id,today.month,today.day,today.month,today.day,int(calendar.isleap(today.year)))) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def save_profile(self, guild_id, user_id, bio, theme):
        bio = text(bio,"Profile bio",0,160)
        if theme not in THEMES:
            raise ValueError("Choose rose, lavender, or mocha.")
        async with self.lock:
            await self.db.execute("INSERT OR REPLACE INTO hub_profiles VALUES (?,?,?,?)", (guild_id,user_id,bio,theme))
            await self.db.commit()

    async def profile(self, guild_id, user_id):
        async with self.lock:
            async with self.db.execute("SELECT bio,theme FROM hub_profiles WHERE guild_id=? AND user_id=?", (guild_id,user_id)) as cursor:
                row = await cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            async with self.db.execute("SELECT COUNT(*) FROM hub_thanks WHERE guild_id=? AND recipient_id=?", (guild_id,user_id)) as cursor:
                result["thanks"] = (await cursor.fetchone())[0]
            async with self.db.execute("SELECT name FROM hub_badges WHERE guild_id=? AND user_id=? ORDER BY name LIMIT 12", (guild_id,user_id)) as cursor:
                result["badges"] = [item[0] for item in await cursor.fetchall()]
            return result

    async def delete_profile(self, guild_id, user_id):
        async with self.lock:
            for table in ("hub_profiles","hub_badges"):
                await self.db.execute(f"DELETE FROM {table} WHERE guild_id=? AND user_id=?", (guild_id,user_id))
            await self.db.execute("DELETE FROM hub_thanks WHERE guild_id=? AND (sender_id=? OR recipient_id=?)", (guild_id,user_id,user_id))
            await self.db.commit()

    async def give_thanks(self, guild_id, sender_id, recipient_id, source, *, now=None):
        now = time.time() if now is None else now
        if sender_id == recipient_id:
            raise ValueError("Choose another community member to thank.")
        async with self.lock:
            async with self.db.execute("SELECT recipient_id FROM hub_thanks_limits WHERE guild_id=? AND source=?", (guild_id,source)) as cursor:
                prior = await cursor.fetchone()
            if prior:
                if prior[0] != recipient_id:
                    raise ValueError("That thank-you request was already used.")
                return False
            async with self.db.execute("SELECT COUNT(*) FROM hub_profiles WHERE guild_id=? AND user_id IN (?,?)", (guild_id,sender_id,recipient_id)) as cursor:
                if (await cursor.fetchone())[0] != 2:
                    raise ValueError("Both members must opt into café profiles first with /profile edit.")
            async with self.db.execute("SELECT recipient_id FROM hub_thanks_limits WHERE guild_id=? AND sender_id=? AND created_at>?", (guild_id,sender_id,now-86400)) as cursor:
                recipients = [row[0] for row in await cursor.fetchall()]
            if len(recipients) >= 3 or recipient_id in recipients:
                raise ValueError("Give up to three thanks in 24 hours, once per recipient.")
            await self.db.execute("INSERT INTO hub_thanks VALUES (?,?,?,?,?)", (guild_id,sender_id,recipient_id,source,now))
            await self.db.execute("INSERT INTO hub_thanks_limits VALUES (?,?,?,?,?)", (guild_id,sender_id,recipient_id,source,now))
            await self.db.commit()
        return True

    async def badge(self, guild_id, user_id, name, actor_id, remove=False):
        name = text(name,"Badge name",1,40)
        async with self.lock:
            async with self.db.execute("SELECT 1 FROM hub_profiles WHERE guild_id=? AND user_id=?", (guild_id,user_id)) as cursor:
                if not await cursor.fetchone():
                    raise ValueError("This member has not opted into café profiles.")
            if remove:
                await self.db.execute("DELETE FROM hub_badges WHERE guild_id=? AND user_id=? AND name=?", (guild_id,user_id,name))
            else:
                async with self.db.execute("SELECT COUNT(*) FROM hub_badges WHERE guild_id=? AND user_id=?", (guild_id,user_id)) as cursor:
                    if (await cursor.fetchone())[0] >= 12:
                        raise ValueError("A profile can have up to 12 badges.")
                await self.db.execute("INSERT OR IGNORE INTO hub_badges VALUES (?,?,?,?)", (guild_id,user_id,name,actor_id))
            await self.db.commit()

    async def highlight_optout(self, guild_id, user_id, enabled=None):
        async with self.lock:
            if enabled is not None:
                await self.db.execute("INSERT OR REPLACE INTO hub_preferences VALUES (?,?,?)", (guild_id,user_id,int(enabled)))
                await self.db.commit()
            async with self.db.execute("SELECT highlight_optout FROM hub_preferences WHERE guild_id=? AND user_id=?", (guild_id,user_id)) as cursor:
                row = await cursor.fetchone()
                return bool(row and row[0])

    async def faq_entries(self, guild_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM hub_faq WHERE guild_id=? ORDER BY name LIMIT 100", (guild_id,)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def save_faq(self, guild_id, actor_id, name, answer, summary, version, remove=False):
        from .store import SettingsConflict
        if type(name) is not str or not re.fullmatch(r"[a-z0-9_-]{1,32}",name):
            raise ValueError("FAQ names use 1–32 lowercase letters, numbers, underscores, or hyphens.")
        answer, summary = text(answer,"Answer",1,1800), text(summary,"Summary",0,80)
        async with self.lock:
            async with self.db.execute("SELECT version FROM hub_faq WHERE guild_id=? AND name=?", (guild_id,name)) as cursor:
                row = await cursor.fetchone()
            if type(version) is not int or version != (row[0] if row else 0):
                raise SettingsConflict("This saved reply changed. Refresh before editing it.")
            if remove:
                await self.db.execute("DELETE FROM hub_faq WHERE guild_id=? AND name=?", (guild_id,name))
            else:
                async with self.db.execute("SELECT COUNT(*) FROM hub_faq WHERE guild_id=?", (guild_id,)) as cursor:
                    if row is None and (await cursor.fetchone())[0] >= 100:
                        raise ValueError("A server can keep 100 saved replies.")
                await self.db.execute("INSERT OR REPLACE INTO hub_faq VALUES (?,?,?,?,?,?)", (guild_id,name,answer,summary,version+1,actor_id))
            await self.db.commit()

    async def claim_hub_delivery(self, guild_id, source):
        async with self.lock:
            cursor = await self.db.execute("INSERT OR IGNORE INTO hub_deliveries VALUES (?,?,'sending','',?)", (guild_id,source,time.time()))
            await self.db.commit()
            return bool(cursor.rowcount)

    async def finish_hub_delivery(self, guild_id, source, status, outcome=""):
        async with self.lock:
            await self.db.execute("UPDATE hub_deliveries SET status=?,outcome=? WHERE guild_id=? AND source=?", (status,outcome[:300],guild_id,source))
            await self.db.commit()

    async def hub_deliveries(self, guild_id):
        async with self.lock:
            async with self.db.execute("SELECT source,status,outcome,created_at FROM hub_deliveries WHERE guild_id=? AND status!='sent' ORDER BY created_at DESC LIMIT 30", (guild_id,)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def role_grants(self, guild_id=None, purpose=None):
        query,args = "SELECT * FROM hub_role_grants WHERE 1=1",[]
        for key,value in (("guild_id",guild_id),("purpose",purpose)):
            if value is not None:
                query += f" AND {key}=?"; args.append(value)
        async with self.lock:
            async with self.db.execute(query+" ORDER BY expires_at",args) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def write_role_grant(self, guild_id, purpose, user_id, role_id, expires_at, status="pending"):
        async with self.lock:
            await self.db.execute("INSERT OR REPLACE INTO hub_role_grants VALUES (?,?,?,?,?,?)", (guild_id,purpose,user_id,role_id,expires_at,status))
            await self.db.commit()

    async def forget_hub_role(self, row):
        async with self.lock:
            await self.db.execute("DELETE FROM hub_role_grants WHERE guild_id=? AND purpose=? AND user_id=? AND role_id=?", (row["guild_id"],row["purpose"],row["user_id"],row["role_id"]))
            await self.db.commit()

    async def expire_event_roles(self, guild_id, item_id):
        async with self.lock:
            await self.db.execute("UPDATE hub_role_grants SET expires_at=0 WHERE guild_id=? AND purpose=?", (guild_id,f"event:{item_id}"))
            await self.db.commit()

    async def purge_hub_private_data(self, now):
        async with self.lock:
            # Closed ticket content and transcripts expire after 30 days.
            await self.db.execute("UPDATE hub_items SET data=json_remove(data,'$.transcript','$.transcript_until') WHERE kind='ticket' AND json_extract(data,'$.transcript_until')<=?", (now,))
            await self.db.execute("UPDATE hub_items SET data='{}' WHERE kind='ticket' AND status='closed' AND updated_at<? AND data!='{}'", (now-30*86400,))
            await self.db.execute("DELETE FROM hub_deliveries WHERE created_at<? AND status='sent'", (now-400*86400,))
            await self.db.execute("DELETE FROM hub_thanks_limits WHERE created_at<?", (now-86400,))
            await self.db.commit()
