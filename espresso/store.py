"""Single-process SQLite persistence, with serialized transactions."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import time

import aiosqlite

from .rules import Settings
from .reaction_roles import mapping_key


class SettingsConflict(ValueError):
    """The settings changed after the caller opened its form."""


class Store:
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
        """)
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.close()

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
                "AND source LIKE 'message:%' AND created_at>? LIMIT 1",
                (guild_id, user_id, now - 30),
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
                await self.db.commit()
            async with self.db.execute(
                "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=? AND created_at>=?",
                (guild_id, user_id, now - window_hours * 3600),
            ) as cur:
                count = (await cur.fetchone())[0]
            return warning_id, count

    async def warnings(self, guild_id: int, user_id: int):
        async with self.lock:
            async with self.db.execute(
                "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
                (guild_id, user_id),
            ) as cur:
                return await cur.fetchall()

    async def clear(self, guild_id: int, user_id: int) -> int:
        async with self.lock:
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
            await self.db.execute("DELETE FROM settings WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM warnings WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM settings_versions WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM settings_audit WHERE guild_id=?", (guild_id,))
            await self.db.execute("DELETE FROM reaction_role_grants WHERE guild_id=?", (guild_id,))
            await self.db.commit()
