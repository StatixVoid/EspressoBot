"""Durable staff inbox, moderation cases, scheduled posts and raid recovery."""

import json
import hashlib
import time


class CommunityStore:
    async def open_community(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS moderation_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                user_id INTEGER, actor_id INTEGER NOT NULL, kind TEXT NOT NULL,
                reason TEXT NOT NULL, outcome TEXT NOT NULL, source TEXT NOT NULL,
                warning_id INTEGER, created_at REAL NOT NULL, UNIQUE(guild_id,source)
            );
            CREATE INDEX IF NOT EXISTS cases_guild ON moderation_cases(guild_id,id);
            CREATE TABLE IF NOT EXISTS warning_voids (
                guild_id INTEGER NOT NULL, warning_id INTEGER NOT NULL,
                actor_id INTEGER NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(guild_id,warning_id)
            );
            CREATE TABLE IF NOT EXISTS staff_inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                kind TEXT NOT NULL, author_id INTEGER NOT NULL, target_id INTEGER,
                message_url TEXT NOT NULL, case_id INTEGER, body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', assigned_id INTEGER,
                resolution TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                source TEXT NOT NULL, UNIQUE(guild_id,source)
            );
            CREATE INDEX IF NOT EXISTS inbox_guild ON staff_inbox(guild_id,id);
            CREATE TABLE IF NOT EXISTS inbox_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                inbox_id INTEGER NOT NULL, actor_id INTEGER NOT NULL,
                action TEXT NOT NULL, note TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL, actor_id INTEGER NOT NULL,
                content TEXT NOT NULL, due_at REAL NOT NULL, repeat_days INTEGER NOT NULL,
                notify INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                outcome TEXT NOT NULL DEFAULT '', message_id INTEGER, source TEXT NOT NULL,
                created_at REAL NOT NULL, UNIQUE(guild_id,source)
            );
            CREATE INDEX IF NOT EXISTS posts_due ON scheduled_posts(status,due_at);
            CREATE TABLE IF NOT EXISTS raid_recovery (
                guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                data TEXT NOT NULL, restore_at REAL NOT NULL,
                PRIMARY KEY(guild_id,channel_id)
            );
            CREATE TABLE IF NOT EXISTS community_state (
                guild_id INTEGER NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(guild_id,name)
            );
            INSERT OR IGNORE INTO moderation_cases
                (guild_id,user_id,actor_id,kind,reason,outcome,source,warning_id,created_at)
                SELECT guild_id,user_id,moderator_id,'warning',reason,'Warning recorded',
                    'warning:' || id,id,created_at FROM warnings;
        """)
        async with self.db.execute("PRAGMA table_info(scheduled_posts)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        if "request_hash" not in columns:
            await self.db.execute("ALTER TABLE scheduled_posts ADD COLUMN request_hash TEXT NOT NULL DEFAULT ''")
        # A process dying during a send leaves an uncertain result. Never replay it blindly.
        await self.db.execute("UPDATE scheduled_posts SET status='uncertain',outcome='Process stopped during delivery; check Discord before rescheduling.' WHERE status='sending'")
        await self.db.commit()

    async def record_case(self, guild_id, user_id, actor_id, kind, reason, outcome, source, warning_id=None):
        async with self.lock:
            await self.db.execute("INSERT OR IGNORE INTO moderation_cases(guild_id,user_id,actor_id,kind,reason,outcome,source,warning_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (guild_id,user_id,actor_id,kind,reason[:1000],outcome[:1500],source,warning_id,time.time()))
            await self.db.commit()
            async with self.db.execute("SELECT id FROM moderation_cases WHERE guild_id=? AND source=?", (guild_id,source)) as cur:
                return (await cur.fetchone())[0]

    async def cases(self, guild_id, before=None, user_id=None):
        query, args = "SELECT c.*,v.created_at AS voided_at FROM moderation_cases c LEFT JOIN warning_voids v ON v.guild_id=c.guild_id AND v.warning_id=c.warning_id WHERE c.guild_id=?", [guild_id]
        if before is not None:
            query += " AND c.id<?"
            args.append(before)
        if user_id is not None:
            query += " AND c.user_id=?"
            args.append(user_id)
        async with self.lock:
            settings = await self._settings(guild_id)
            async with self.db.execute(query+" ORDER BY c.id DESC LIMIT 51", args) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                row["expires_at"] = row["created_at"]+settings.warning_expiry_days*86400 if row["warning_id"] and settings.warning_expiry_days else None
                row["expired"] = row["expires_at"] is not None and row["expires_at"] <= time.time()
            return rows

    async def case(self, guild_id, case_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM moderation_cases WHERE guild_id=? AND id=?", (guild_id,case_id)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def warning(self, guild_id, warning_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM warnings WHERE guild_id=? AND id=?", (guild_id,warning_id)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def void_warning(self, guild_id, warning_id, actor_id, reason):
        if type(reason) is not str or not 1 <= len(reason.strip()) <= 1000:
            raise ValueError("Give a reason of 1–1000 characters.")
        async with self.lock:
            async with self.db.execute("SELECT user_id FROM warnings WHERE guild_id=? AND id=?", (guild_id,warning_id)) as cur:
                if await cur.fetchone() is None:
                    raise ValueError("That warning no longer exists in this server.")
            async with self.db.execute("INSERT OR IGNORE INTO warning_voids VALUES (?,?,?,?,?)", (guild_id,warning_id,actor_id,reason,time.time())) as cur:
                changed = bool(cur.rowcount)
            await self.db.commit()
            return changed

    async def create_inbox(self, guild_id, kind, author_id, body, source, *, target_id=None, message_url="", case_id=None, now=None):
        now = time.time() if now is None else now
        if kind not in ("report", "appeal") or type(body) is not str or not 1 <= len(body.strip()) <= 1500:
            raise ValueError("Write a report or appeal of 1–1500 characters.")
        async with self.lock:
            async with self.db.execute("SELECT id FROM staff_inbox WHERE guild_id=? AND source=?", (guild_id,source)) as cur:
                existing = await cur.fetchone()
                if existing:
                    return existing[0]
            async with self.db.execute("SELECT COUNT(*) AS total,MAX(created_at) AS latest FROM staff_inbox WHERE guild_id=? AND author_id=? AND (created_at>? OR status IN ('open','in_review'))", (guild_id,author_id,now-60)) as cur:
                row = await cur.fetchone()
            if (row["latest"] is not None and row["latest"] > now-60) or row["total"] >= 5:
                raise ValueError("Please wait a minute between submissions. You may have up to five open items.")
            if kind == "appeal":
                async with self.db.execute("SELECT user_id FROM moderation_cases WHERE guild_id=? AND id=?", (guild_id,case_id)) as cur:
                    case = await cur.fetchone()
                if case is None or case[0] != author_id:
                    raise ValueError("You can appeal only your own moderation case from this server.")
                async with self.db.execute("SELECT 1 FROM staff_inbox WHERE guild_id=? AND case_id=? AND status IN ('open','in_review')", (guild_id,case_id)) as cur:
                    if await cur.fetchone():
                        raise ValueError("An appeal for this case is already waiting for review.")
            async with self.db.execute("INSERT INTO staff_inbox(guild_id,kind,author_id,target_id,message_url,case_id,body,created_at,updated_at,source) VALUES (?,?,?,?,?,?,?,?,?,?)", (guild_id,kind,author_id,target_id,message_url,case_id,body.strip(),now,now,source)) as cur:
                item_id = cur.lastrowid
            await self.db.commit()
            return item_id

    async def inbox(self, guild_id, before=None, status="open"):
        query, args = "SELECT * FROM staff_inbox WHERE guild_id=?", [guild_id]
        if status != "all":
            query += " AND status IN ('open','in_review')" if status == "open" else " AND status IN ('resolved','rejected')"
        if before is not None:
            query += " AND id<?"
            args.append(before)
        async with self.lock:
            async with self.db.execute(query+" ORDER BY id DESC LIMIT 51", args) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def inbox_history(self, guild_id, item_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM inbox_audit WHERE guild_id=? AND inbox_id=? ORDER BY id", (guild_id,item_id)) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def review_inbox(self, guild_id, item_id, actor_id, version, action, note=""):
        from .store import SettingsConflict
        if action not in ("claim", "resolve", "reject", "reopen") or type(note) is not str or len(note) > 1000:
            raise ValueError("Invalid review action or note.")
        if action in ("resolve", "reject") and not note.strip():
            raise ValueError("Add a resolution note.")
        async with self.lock:
            async with self.db.execute("SELECT * FROM staff_inbox WHERE guild_id=? AND id=?", (guild_id,item_id)) as cur:
                row = await cur.fetchone()
            if row is None:
                raise ValueError("This item does not exist in this server.")
            if type(version) is not int or row["version"] != version:
                raise SettingsConflict("This item changed. Refresh before reviewing it again.")
            if row["author_id"] == actor_id:
                raise ValueError("Another staff member must review your submission.")
            closed = row["status"] in ("resolved", "rejected")
            if (action == "reopen") != closed:
                raise ValueError("Reopen closed items before reviewing them.")
            status = {"claim":"in_review","resolve":"resolved","reject":"rejected","reopen":"open"}[action]
            now = time.time()
            try:
                await self.db.execute("UPDATE staff_inbox SET status=?,assigned_id=?,resolution=?,version=version+1,updated_at=? WHERE guild_id=? AND id=?", (status,None if action == "reopen" else actor_id,note,now,guild_id,item_id))
                await self.db.execute("INSERT INTO inbox_audit(guild_id,inbox_id,actor_id,action,note,created_at) VALUES (?,?,?,?,?,?)", (guild_id,item_id,actor_id,action,note,now))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise
            return status

    async def schedule_post(self, guild_id, channel_id, actor_id, content, due_at, repeat_days, notify, source, now=None):
        import math
        now = time.time() if now is None else now
        if type(content) is not str or not 1 <= len(content.strip()) <= 1800:
            raise ValueError("Posts must contain 1–1800 characters.")
        if type(due_at) not in (int,float) or not math.isfinite(due_at):
            raise ValueError("Schedule between 10 seconds and one year from now.")
        if type(repeat_days) is not int or not 0 <= repeat_days <= 365 or type(notify) is not bool:
            raise ValueError("Choose 0–365 repeat days and a notification switch.")
        fingerprint = hashlib.sha256(json.dumps([channel_id,actor_id,content.strip(),float(due_at),repeat_days,notify],ensure_ascii=True).encode()).hexdigest()
        async with self.lock:
            async with self.db.execute("SELECT id,request_hash FROM scheduled_posts WHERE guild_id=? AND source=?", (guild_id,source)) as cur:
                existing = await cur.fetchone()
            if existing:
                if existing["request_hash"] and existing["request_hash"] != fingerprint:
                    raise ValueError("This request ID already belongs to a different scheduled post.")
                return existing["id"]
            if not now+10 <= due_at <= now+366*86400:
                raise ValueError("Schedule between 10 seconds and one year from now.")
            async with self.db.execute("SELECT COUNT(*) FROM scheduled_posts WHERE guild_id=? AND status='pending'", (guild_id,)) as cur:
                if (await cur.fetchone())[0] >= 100:
                    raise ValueError("This server already has 100 scheduled posts.")
            await self.db.execute("INSERT INTO scheduled_posts(guild_id,channel_id,actor_id,content,due_at,repeat_days,notify,source,created_at,request_hash) VALUES (?,?,?,?,?,?,?,?,?,?)", (guild_id,channel_id,actor_id,content.strip(),due_at,repeat_days,notify,source,now,fingerprint))
            await self.db.commit()
            async with self.db.execute("SELECT id FROM scheduled_posts WHERE guild_id=? AND source=?", (guild_id,source)) as cur:
                return (await cur.fetchone())[0]

    async def posts(self, guild_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM scheduled_posts WHERE guild_id=? ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END,due_at DESC LIMIT 100", (guild_id,)) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def cancel_post(self, guild_id, post_id):
        async with self.lock:
            async with self.db.execute("UPDATE scheduled_posts SET status='cancelled' WHERE guild_id=? AND id=? AND status='pending'", (guild_id,post_id)) as cur:
                changed = bool(cur.rowcount)
            await self.db.commit()
            if not changed:
                raise ValueError("Only a pending post can be cancelled. Refresh to check its status.")

    async def claim_due_posts(self, now):
        async with self.lock:
            async with self.db.execute("SELECT * FROM scheduled_posts WHERE status='pending' AND due_at<=? ORDER BY due_at LIMIT 20", (now,)) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                await self.db.execute("UPDATE scheduled_posts SET status='sending' WHERE id=?", (row["id"],))
            await self.db.commit()
            return rows

    async def finish_post(self, row, status, outcome, message_id=None, now=None):
        now = time.time() if now is None else now
        due = row["due_at"]
        if status == "sent" and row["repeat_days"]:
            interval = row["repeat_days"]*86400
            due += (int(max(0,now-due)//interval)+1)*interval
            status = "pending"
        async with self.lock:
            await self.db.execute("UPDATE scheduled_posts SET status=?,outcome=?,message_id=?,due_at=? WHERE id=? AND status='sending'", (status,outcome[:500],message_id,due,row["id"]))
            await self.db.commit()

    async def recovery(self, guild_id=None):
        async with self.lock:
            async with self.db.execute("SELECT * FROM raid_recovery" + (" WHERE guild_id=?" if guild_id is not None else ""), (guild_id,) if guild_id is not None else ()) as cur:
                return [{**dict(row),"data":json.loads(row["data"])} for row in await cur.fetchall()]

    async def save_recovery(self, guild_id, channel_id, data, restore_at):
        async with self.lock:
            await self.db.execute("INSERT INTO raid_recovery VALUES (?,?,?,?) ON CONFLICT(guild_id,channel_id) DO UPDATE SET data=excluded.data,restore_at=excluded.restore_at", (guild_id,channel_id,json.dumps(data),restore_at))
            await self.db.commit()

    async def forget_recovery(self, guild_id, channel_id):
        async with self.lock:
            await self.db.execute("DELETE FROM raid_recovery WHERE guild_id=? AND channel_id=?", (guild_id,channel_id))
            await self.db.commit()

    async def state(self, guild_id, name):
        async with self.lock:
            async with self.db.execute("SELECT value FROM community_state WHERE guild_id=? AND name=?", (guild_id,name)) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def set_state(self, guild_id, name, value):
        async with self.lock:
            await self.db.execute("INSERT INTO community_state VALUES (?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET value=excluded.value", (guild_id,name,str(value)))
            await self.db.commit()
