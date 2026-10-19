"""Additive storage for dashboard refinements; all mutations share Store.lock."""

from dataclasses import asdict
import json
import time
from .hub_store import HubStore


class RefinementStore(HubStore):
    async def open_refinements(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings_history (
                guild_id INTEGER NOT NULL, version INTEGER NOT NULL, actor_id INTEGER,
                changed_keys TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(guild_id,version)
            );
            INSERT OR IGNORE INTO settings_history
                SELECT s.guild_id,COALESCE(v.version,0),NULL,'[]',s.data,0
                FROM settings s LEFT JOIN settings_versions v USING(guild_id);
            CREATE TABLE IF NOT EXISTS role_menus (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                actor_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER,
                data TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL,
                created_at REAL NOT NULL, UNIQUE(guild_id,source)
            );
            CREATE TABLE IF NOT EXISTS menu_grants (
                guild_id INTEGER NOT NULL, menu_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL, PRIMARY KEY(guild_id,menu_id,user_id,role_id)
            );
        """)
        additions = {
            "staff_inbox":{"public_response":"TEXT NOT NULL DEFAULT ''","notice_status":"TEXT NOT NULL DEFAULT 'not_requested'"},
            "scheduled_posts":{"version":"INTEGER NOT NULL DEFAULT 1","timezone":"TEXT NOT NULL DEFAULT 'UTC'",
                               "recurrence":"TEXT NOT NULL DEFAULT 'interval'","weekdays":"TEXT NOT NULL DEFAULT '[]'","local_time":"TEXT NOT NULL DEFAULT '09:00'"},
        }
        for table, fields in additions.items():
            async with self.db.execute(f"PRAGMA table_info({table})") as cursor:
                existing = {row["name"] for row in await cursor.fetchall()}
            for field, declaration in fields.items():
                if field not in existing:
                    await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {field} {declaration}")
        # A restart must never blindly resend a possibly delivered private notice or menu.
        await self.db.execute("UPDATE staff_inbox SET notice_status='uncertain' WHERE notice_status='sending'")
        await self.db.execute("UPDATE role_menus SET status='uncertain' WHERE status='posting'")

    async def settings_history(self, guild_id, before=None):
        query,args = "SELECT guild_id,version,actor_id,changed_keys,created_at FROM settings_history WHERE guild_id=?",[guild_id]
        if before is not None:
            query += " AND version<?"
            args.append(before)
        async with self.lock:
            async with self.db.execute(query+" ORDER BY version DESC LIMIT 51",args) as cur:
                return [{**dict(row),"changed_keys":json.loads(row["changed_keys"])} for row in await cur.fetchall()]

    async def historical_settings(self, guild_id, version):
        from .rules import Settings
        async with self.lock:
            async with self.db.execute("SELECT data FROM settings_history WHERE guild_id=? AND version=?",(guild_id,version)) as cur:
                row = await cur.fetchone()
            if row is None:
                raise ValueError("That settings version is not available in this server.")
            return Settings(**json.loads(row[0]))

    async def inbox_item(self, guild_id, item_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM staff_inbox WHERE guild_id=? AND id=?",(guild_id,item_id)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def member_reports(self, guild_id, user_id, before=None):
        query,args = "SELECT id,kind,status,public_response,case_id,created_at,updated_at FROM staff_inbox WHERE guild_id=? AND author_id=?",[guild_id,user_id]
        if before is not None:
            query += " AND id<?"
            args.append(before)
        async with self.lock:
            async with self.db.execute(query+" ORDER BY id DESC LIMIT 11",args) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def notice_result(self, guild_id, item_id, version, status):
        async with self.lock:
            await self.db.execute("UPDATE staff_inbox SET notice_status=? WHERE guild_id=? AND id=? AND version=? AND notice_status='sending'",(status,guild_id,item_id,version))
            await self.db.commit()

    async def overview(self, guild_id, *, manager=False, moderator=False):
        result = {}
        async with self.lock:
            if moderator:
                async with self.db.execute("SELECT COUNT(*) total,MIN(created_at) oldest,SUM(CASE WHEN created_at<? THEN 1 ELSE 0 END) overdue FROM staff_inbox WHERE guild_id=? AND status IN ('open','in_review')",(time.time()-48*3600,guild_id)) as cur:
                    row = dict(await cur.fetchone())
                result["reports"] = {**row,"overdue":row["overdue"] or 0}
                async with self.db.execute("SELECT id,kind,status,created_at FROM staff_inbox WHERE guild_id=? AND status IN ('open','in_review') ORDER BY created_at LIMIT 5",(guild_id,)) as cur:
                    result["waiting"] = [dict(row) for row in await cur.fetchall()]
                async with self.db.execute("SELECT reason,COUNT(*) total FROM warnings WHERE guild_id=? AND source LIKE 'message:%' AND created_at>? GROUP BY reason ORDER BY total DESC LIMIT 5",(guild_id,time.time()-7*86400)) as cur:
                    result["top_rules"] = [dict(row) for row in await cur.fetchall()]
            if manager:
                async with self.db.execute("SELECT id,status,outcome,channel_id FROM scheduled_posts WHERE guild_id=? AND status IN ('failed','uncertain') ORDER BY due_at DESC LIMIT 10",(guild_id,)) as cur:
                    result["post_issues"] = [dict(row) for row in await cur.fetchall()]
                async with self.db.execute("SELECT COUNT(*) FROM screening_cases WHERE guild_id=? AND status IN ('pending','awaiting_agreement')",(guild_id,)) as cur:
                    result["verification_waiting"] = (await cur.fetchone())[0]
        return result

    async def post(self, guild_id, post_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM scheduled_posts WHERE guild_id=? AND id=?",(guild_id,post_id)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def change_post(self, guild_id, post_id, version, action, actor_id, fields=None, acknowledge=False):
        from .store import SettingsConflict
        fields = fields or {}
        if action not in ("edit","pause","resume","retry","cancel"):
            raise ValueError("Unknown scheduled-post action.")
        allowed = {"channel_id","content","due_at","repeat_days","notify","timezone","recurrence","weekdays","local_time"}
        if not fields.keys() <= allowed:
            raise ValueError("Unknown scheduled-post fields.")
        async with self.lock:
            async with self.db.execute("SELECT * FROM scheduled_posts WHERE guild_id=? AND id=?",(guild_id,post_id)) as cur:
                row = await cur.fetchone()
            if row is None:
                raise ValueError("That post is not in this server.")
            if type(version) is not int or version != row["version"]:
                raise SettingsConflict("This post changed. Refresh before trying again.")
            valid = {"edit":("pending","paused"),"pause":("pending",),"resume":("paused",),"retry":("failed","uncertain"),"cancel":("pending","paused")}
            if row["status"] not in valid[action]:
                raise ValueError("This post cannot be changed in its current delivery state.")
            if action == "retry" and row["status"] == "uncertain" and acknowledge is not True:
                raise ValueError("Check Discord first and explicitly confirm that you want another delivery attempt.")
            status = {"edit":row["status"],"pause":"paused","resume":"pending","retry":"pending","cancel":"cancelled"}[action]
            if status == "pending" and fields.get("due_at",row["due_at"]) < time.time()+10:
                raise ValueError("Choose a new delivery time at least 10 seconds from now.")
            assignments = ["status=?","version=version+1","actor_id=?"]
            values = [status,actor_id]
            for key,value in fields.items():
                assignments.append(key+"=?")
                values.append(value)
            if action == "retry":
                assignments.append("outcome=?")
                values.append("Staff checked delivery and scheduled a new attempt.")
            await self.db.execute("UPDATE scheduled_posts SET "+",".join(assignments)+" WHERE guild_id=? AND id=?",(*values,guild_id,post_id))
            await self.db.commit()
            return status

    async def extend_protection(self, guild_id, channel_id, minutes):
        if type(minutes) is not int or not 1 <= minutes <= 60:
            raise ValueError("Extend by 1–60 minutes.")
        async with self.lock:
            async with self.db.execute("UPDATE raid_recovery SET restore_at=MIN(MAX(restore_at,?)+?,?) WHERE guild_id=? AND channel_id=?",(time.time(),minutes*60,time.time()+3600,guild_id,channel_id)) as cur:
                changed = bool(cur.rowcount)
            await self.db.commit()
            if not changed:
                raise ValueError("This channel no longer has temporary protection.")

    async def menus(self, guild_id=None):
        async with self.lock:
            async with self.db.execute("SELECT * FROM role_menus"+(" WHERE guild_id=?" if guild_id is not None else "")+" ORDER BY id DESC",(guild_id,) if guild_id is not None else ()) as cur:
                return [{**dict(row),"data":json.loads(row["data"])} for row in await cur.fetchall()]

    async def menu(self, guild_id, menu_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM role_menus WHERE guild_id=? AND id=?",(guild_id,menu_id)) as cur:
                row = await cur.fetchone()
                return {**dict(row),"data":json.loads(row["data"])} if row else None

    async def begin_menu(self, guild_id, actor_id, channel_id, data, source):
        async with self.lock:
            async with self.db.execute("SELECT id,data FROM role_menus WHERE guild_id=? AND source=?",(guild_id,source)) as cur:
                row = await cur.fetchone()
            encoded = json.dumps(data,sort_keys=True)
            if row:
                if json.loads(row["data"]) != data:
                    raise ValueError("This request ID belongs to different menu choices.")
                return row["id"],False
            async with self.db.execute("SELECT COUNT(*) FROM role_menus WHERE guild_id=? AND status IN ('active','posting','uncertain')",(guild_id,)) as cur:
                if (await cur.fetchone())[0] >= 25:
                    raise ValueError("This server has reached its 25-menu limit. Disable an old menu first.")
            async with self.db.execute("INSERT INTO role_menus(guild_id,actor_id,channel_id,data,status,source,created_at) VALUES (?,?,?,?,'posting',?,?)",(guild_id,actor_id,channel_id,encoded,source,time.time())) as cur:
                menu_id = cur.lastrowid
            await self.db.commit()
            return menu_id,True

    async def finish_menu(self, guild_id, menu_id, status, message_id=None):
        async with self.lock:
            await self.db.execute("UPDATE role_menus SET status=?,message_id=COALESCE(?,message_id) WHERE guild_id=? AND id=?",(status,message_id,guild_id,menu_id))
            await self.db.commit()

    async def menu_grants(self, guild_id, menu_id, user_id):
        async with self.lock:
            async with self.db.execute("SELECT role_id FROM menu_grants WHERE guild_id=? AND menu_id=? AND user_id=?",(guild_id,menu_id,user_id)) as cur:
                return {row[0] for row in await cur.fetchall()}

    async def save_menu_grants(self, guild_id, menu_id, user_id, role_ids):
        async with self.lock:
            try:
                await self.db.execute("DELETE FROM menu_grants WHERE guild_id=? AND menu_id=? AND user_id=?",(guild_id,menu_id,user_id))
                await self.db.executemany("INSERT INTO menu_grants VALUES (?,?,?,?)",[(guild_id,menu_id,user_id,role_id) for role_id in role_ids])
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise
