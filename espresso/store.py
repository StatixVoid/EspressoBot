"""Single-process SQLite persistence, with serialized transactions."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import time

import aiosqlite

from .rules import Settings
from .reaction_roles import mapping_key
from .community_store import CommunityStore


class SettingsConflict(ValueError):
    """The settings changed after the caller opened its form."""


class Store(CommunityStore):
    def __init__(self, path: str):
        self.path = path
        self.lock = asyncio.Lock()
        self.db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER PRIMARY KEY, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings_versions (
                guild_id INTEGER PRIMARY KEY, version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL, actor_id INTEGER NOT NULL,
                changed_keys TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reaction_role_grants (
                guild_id INTEGER NOT NULL, mapping_key TEXT NOT NULL,
                user_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
                PRIMARY KEY(guild_id, mapping_key, user_id)
            );
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL, reason TEXT NOT NULL,
                created_at REAL NOT NULL, source TEXT NOT NULL,
                UNIQUE(guild_id, source)
            );
            CREATE INDEX IF NOT EXISTS warnings_member
                ON warnings(guild_id, user_id, created_at);
            CREATE TABLE IF NOT EXISTS screening_cases (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                joined_at REAL NOT NULL, account_created_at REAL NOT NULL,
                requires_review INTEGER NOT NULL, agreed_at REAL,
                rules_hash TEXT NOT NULL, status TEXT NOT NULL,
                reviewer_id INTEGER, role_id INTEGER, updated_at REAL NOT NULL,
                version INTEGER NOT NULL,
                PRIMARY KEY(guild_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS screening_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL, actor_id INTEGER, action TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                kind TEXT NOT NULL, title TEXT NOT NULL, details TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS activity_guild ON activity_logs(guild_id,id);
            CREATE TABLE IF NOT EXISTS role_template_posts (
                guild_id INTEGER NOT NULL, request_id TEXT NOT NULL,
                data TEXT NOT NULL, PRIMARY KEY(guild_id,request_id)
            );
        """)
        await self.db.execute("DELETE FROM activity_logs WHERE created_at<?",(time.time()-30*86400,))
        await self.db.commit()

        await self.open_community()

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    async def template_operation(self, guild_id, request_id):
        async with self.lock:
            async with self.db.execute("SELECT data FROM role_template_posts WHERE guild_id=? AND request_id=?", (guild_id,request_id)) as cur:
                row = await cur.fetchone()
            return json.loads(row[0]) if row else None

    async def begin_template_operation(self, guild_id, request_id, version, data):
        async with self.lock:
            if await self._version(guild_id) != version:
                raise SettingsConflict("Settings changed elsewhere. Refresh this server before posting a template.")
            try:
                await self.db.execute("INSERT INTO role_template_posts VALUES (?,?,?)", (guild_id,request_id,json.dumps(data)))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def write_template_operation(self, guild_id, request_id, data):
        async with self.lock:
            try:
                await self.db.execute("UPDATE role_template_posts SET data=? WHERE guild_id=? AND request_id=?", (json.dumps(data),guild_id,request_id))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def commit_template_operation(self, guild_id, request_id, version, actor_id, changes, operation):
        async with self.lock:
            if await self._version(guild_id) != version:
                raise SettingsConflict("Settings changed while posting. Refresh this server before trying again.")
            settings = Settings(**(asdict(await self._settings(guild_id)) | changes))
            try:
                await self.db.execute("UPDATE role_template_posts SET data=? WHERE guild_id=? AND request_id=?", (json.dumps(operation),guild_id,request_id))
                # This commits the outcome and settings/version/audit together.
                await self._save(guild_id, settings, actor_id, sorted(changes))
            except BaseException:
                await self.db.rollback()
                raise

    async def _settings(self, guild_id: int) -> Settings:
        async with self.db.execute("SELECT data FROM settings WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
        return Settings(**json.loads(row[0])) if row else Settings()

    async def settings(self, guild_id: int) -> Settings:
        async with self.lock:
            return await self._settings(guild_id)

    async def _version(self, guild_id: int) -> int:
        async with self.db.execute("SELECT version FROM settings_versions WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def snapshot(self, guild_id: int) -> tuple[Settings, int]:
        async with self.lock:
            return await self._settings(guild_id), await self._version(guild_id)

    async def update_from_web(self, guild_id: int, version: int, actor_id: int, changes: dict) -> tuple[Settings, int]:
        async with self.lock:
            if await self._version(guild_id) != version:
                raise SettingsConflict("Settings changed elsewhere. Reload before saving again.")
            data = asdict(await self._settings(guild_id))
            if not changes or not changes.keys() <= data.keys():
                raise ValueError("Supply known settings fields.")
            data.update(changes)
            settings = Settings(**data)
            await self._save(guild_id, settings, actor_id, sorted(changes))
            return settings, await self._version(guild_id)

    async def update(self, guild_id: int, **changes) -> Settings:
        async with self.lock:
            value = await self._settings(guild_id)
            data = asdict(value)
            if not changes.keys() <= data.keys():
                raise ValueError("Unknown setting")
            data.update(changes)
            value = Settings(**data)
            await self._save(guild_id, value)
            return value

    async def edit_list(self, guild_id: int, name: str, item, add: bool) -> None:
        if name not in {"bad_words", "exempt_roles"}:
            raise ValueError("Unknown list")
        async with self.lock:
            settings = await self._settings(guild_id)
            values = getattr(settings, name)
            if add and item not in values:
                if len(values) >= 100:
                    raise ValueError("This list has reached its 100-item limit.")
                values.append(item)
            elif not add and item in values:
                values.remove(item)
            await self._save(guild_id, settings)

    async def edit_hate_filter(self, guild_id: int, category: str, **changes) -> None:
        async with self.lock:
            settings = await self._settings(guild_id)
            if category not in settings.hate_filters:
                raise ValueError("Unknown moderation category.")
            settings.hate_filters[category].update(changes)
            await self._save(guild_id, settings)

    async def _save(self, guild_id: int, settings: Settings,
                    actor_id: int | None = None, changed_keys: list[str] | None = None) -> None:
        settings = Settings(**asdict(settings))  # Validate list edits too.
        try:
            await self.db.execute(
                "INSERT INTO settings VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET data=excluded.data",
                (guild_id, json.dumps(asdict(settings))),
            )
            await self.db.execute(
                "INSERT INTO settings_versions VALUES (?,1) ON CONFLICT(guild_id) DO UPDATE SET version=version+1",
                (guild_id,),
            )
            if actor_id is not None:
                await self.db.execute(
                    "INSERT INTO settings_audit(guild_id,actor_id,changed_keys,created_at) VALUES (?,?,?,?)",
                    (guild_id, actor_id, json.dumps(changed_keys), time.time()),
                )
            keys = [mapping_key(mapping) for mapping in settings.reaction_roles]
            if keys:
                await self.db.execute(
                    "DELETE FROM reaction_role_grants WHERE guild_id=? AND mapping_key NOT IN (" + ",".join("?" for _ in keys) + ")",
                    (guild_id, *keys),
                )
            else:
                await self.db.execute("DELETE FROM reaction_role_grants WHERE guild_id=?", (guild_id,))
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            raise

    async def warn(self, guild_id: int, user_id: int, moderator_id: int,
                   reason: str, source: str, window_hours: int,
                   automatic: bool = False, now: float | None = None) -> tuple[int | None, int]:
        now = time.time() if now is None else now
        async with self.lock:
            # Throttle automatic warning issuance, while still deleting every violation.
            async with self.db.execute(
                "SELECT 1 FROM warnings WHERE guild_id=? AND user_id=? "
                "AND source LIKE 'message:%' AND created_at>? AND id NOT IN (SELECT warning_id FROM warning_voids WHERE guild_id=?) LIMIT 1",
                (guild_id, user_id, now - 30, guild_id),
            ) as cur:
                throttled = automatic and await cur.fetchone() is not None
            warning_id = None
            if not throttled:
                async with self.db.execute(
                    "INSERT OR IGNORE INTO warnings "
                    "(guild_id,user_id,moderator_id,reason,created_at,source) VALUES (?,?,?,?,?,?)",
                    (guild_id, user_id, moderator_id, reason, now, source),
                ) as cur:
                    if cur.rowcount:
                        warning_id = cur.lastrowid
                if warning_id is not None:
                    await self.db.execute("INSERT INTO moderation_cases(guild_id,user_id,actor_id,kind,reason,outcome,source,warning_id,created_at) VALUES (?,?,?,'warning',?,'Warning recorded',?,?,?)",
                                          (guild_id,user_id,moderator_id,reason,f"warning:{warning_id}",warning_id,now))
                await self.db.commit()
            settings = await self._settings(guild_id)
            cutoff = now - window_hours * 3600
            if settings.warning_expiry_days:
                cutoff = max(cutoff, now-settings.warning_expiry_days*86400)
            async with self.db.execute(
                "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=? AND created_at>=? AND id NOT IN (SELECT warning_id FROM warning_voids WHERE guild_id=?)",
                (guild_id, user_id, cutoff, guild_id),
            ) as cur:
                count = (await cur.fetchone())[0]
            return warning_id, count

    async def warnings(self, guild_id: int, user_id: int):
        async with self.lock:
            async with self.db.execute(
                "SELECT * FROM warnings WHERE guild_id=? AND user_id=? AND id NOT IN (SELECT warning_id FROM warning_voids WHERE guild_id=?) ORDER BY id DESC LIMIT 10",
                (guild_id, user_id, guild_id),
            ) as cur:
                return await cur.fetchall()

    async def clear(self, guild_id: int, user_id: int, actor_id: int = 0) -> int:
        async with self.lock:
            await self.db.execute("INSERT OR IGNORE INTO warning_voids(guild_id,warning_id,actor_id,reason,created_at) SELECT guild_id,id,?,'Warning history cleared',? FROM warnings WHERE guild_id=? AND user_id=?", (actor_id,time.time(),guild_id,user_id))
            async with self.db.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id)) as cur:
                count = cur.rowcount
            await self.db.commit()
            return count

    async def role_grant(self, guild_id: int, key: str, user_id: int) -> bool:
        async with self.lock:
            async with self.db.execute(
                "SELECT 1 FROM reaction_role_grants WHERE guild_id=? AND mapping_key=? AND user_id=?",
                (guild_id, key, user_id),
            ) as cur:
                return await cur.fetchone() is not None

    async def track_role_grant(self, guild_id: int, key: str, user_id: int, role_id: int) -> None:
        async with self.lock:
            await self.db.execute("INSERT OR IGNORE INTO reaction_role_grants VALUES (?,?,?,?)", (guild_id, key, user_id, role_id))
            await self.db.commit()

    async def forget_role_grant(self, guild_id: int, key: str, user_id: int) -> None:
        async with self.lock:
            await self.db.execute("DELETE FROM reaction_role_grants WHERE guild_id=? AND mapping_key=? AND user_id=?", (guild_id, key, user_id))
            await self.db.commit()

    async def delete_guild(self, guild_id: int) -> None:
        async with self.lock:
            for table in ("moderation_cases", "warning_voids", "staff_inbox", "inbox_audit", "scheduled_posts", "raid_recovery", "community_state"):
                await self.db.execute(f"DELETE FROM {table} WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM settings WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM warnings WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM settings_versions WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM settings_audit WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM reaction_role_grants WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM role_template_posts WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM screening_cases WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM screening_audit WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM activity_logs WHERE guild_id=?", (guild_id,))
            await self.db.commit()

    async def log_activity(self, guild_id, title, details):
        title,details = str(title)[:120],str(details)[:4000]
        lower = title.casefold()
        kind = ("verification" if "verif" in lower or "screen" in lower else
                "settings" if "changed" in lower or "settings" in lower else
                "roles" if "role" in lower else
                "moderation" if any(word in lower for word in ("warn","filter","timeout","moderation")) else "system")
        now = time.time()
        async with self.lock:
            try:
                await self.db.execute("INSERT INTO activity_logs(guild_id,kind,title,details,created_at) VALUES (?,?,?,?,?)",(guild_id,kind,title,details,now))
                await self.db.execute("DELETE FROM activity_logs WHERE guild_id=? AND (created_at<? OR id < COALESCE((SELECT id FROM activity_logs WHERE guild_id=? ORDER BY id DESC LIMIT 1 OFFSET 9999),0))",(guild_id,now-30*86400,guild_id))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def activity_logs(self, guild_id, before=None, kind="all"):
        async with self.lock:
            query = "SELECT * FROM activity_logs WHERE guild_id=? AND created_at>=?"
            values = [guild_id,time.time()-30*86400]
            if before is not None:
                query += " AND id<?"
                values.append(before)
            if kind != "all":
                query += " AND kind=?"
                values.append(kind)
            async with self.db.execute(query+" ORDER BY id DESC LIMIT 51",values) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def screening_case(self, guild_id, user_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM screening_cases WHERE guild_id=? AND user_id=?",(guild_id,user_id)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def screening_queue(self, guild_id, after=0):
        async with self.lock:
            async with self.db.execute("SELECT * FROM screening_cases WHERE guild_id=? AND user_id>? AND status IN ('awaiting_agreement','pending','denied') ORDER BY user_id LIMIT 51",(guild_id,after)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def save_screening_case(self, case, action, actor_id=None):
        """Caller serializes member operations; the version also prevents stale reviews."""
        async with self.lock:
            try:
                existing = await self.db.execute("SELECT version FROM screening_cases WHERE guild_id=? AND user_id=?",(case["guild_id"],case["user_id"]))
                row = await existing.fetchone()
                await existing.close()
                if (row[0] if row else 0) != case["version"]:
                    raise SettingsConflict("This screening request changed. Refresh the review queue.")
                case = {**case,"version":case["version"]+1,"updated_at":time.time()}
                columns = ("guild_id","user_id","joined_at","account_created_at","requires_review","agreed_at","rules_hash","status","reviewer_id","role_id","updated_at","version")
                await self.db.execute("INSERT OR REPLACE INTO screening_cases VALUES ("+",".join("?" for _ in columns)+")",tuple(case[key] for key in columns))
                await self.db.execute("INSERT INTO screening_audit(guild_id,user_id,actor_id,action,created_at) VALUES (?,?,?,?,?)",(case["guild_id"],case["user_id"],actor_id,action,case["updated_at"]))
                await self.db.commit()
                return case
            except BaseException:
                await self.db.rollback()
                raise
