import asyncio
import tempfile
from pathlib import Path
import unittest
import sqlite3

from espresso.store import Store


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "espresso.db")
        self.store = Store(self.path)
        await self.store.open()

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def test_persistence_and_server_isolation(self):
        await self.store.update(1, enabled=True, block_links=True)
        await self.store.edit_list(1, "bad_words", "testword", True)
        await self.store.warn(1, 10, 99, "reason", "interaction:1", 24, now=1000)
        self.assertFalse((await self.store.settings(2)).enabled)
        self.assertFalse(await self.store.warnings(2, 10))
        await self.store.close()
        self.store = Store(self.path)
        await self.store.open()
        self.assertTrue((await self.store.settings(1)).enabled)
        self.assertEqual((await self.store.settings(1)).bad_words, ["testword"])
        self.assertEqual(len(await self.store.warnings(1, 10)), 1)

    async def test_deduplication_and_automatic_cooldown(self):
        first, count = await self.store.warn(1, 10, 99, "spam", "message:1", 24, True, now=100)
        self.assertIsNotNone(first)
        self.assertEqual(count, 1)
        self.assertEqual(await self.store.warn(1, 10, 99, "spam", "message:2", 24, True, now=110), (None, 1))
        # Same message cannot create another warning after cooldown.
        self.assertEqual(await self.store.warn(1, 10, 99, "edit", "message:1", 24, True, now=150), (None, 1))
        second, count = await self.store.warn(1, 10, 99, "spam", "message:3", 24, True, now=150)
        self.assertIsNotNone(second)
        self.assertEqual(count, 2)

    async def test_rolling_window_manual_warnings_and_clear(self):
        await self.store.warn(1, 10, 99, "old", "message:1", 1, now=0)
        _, count = await self.store.warn(1, 10, 99, "new", "interaction:2", 1, now=3601)
        self.assertEqual(count, 1)
        self.assertEqual(len(await self.store.warnings(1, 10)), 2)
        self.assertEqual(await self.store.clear(2, 10), 0)
        self.assertEqual(await self.store.clear(1, 10), 2)

    async def test_concurrent_updates_and_warnings(self):
        await asyncio.gather(*(self.store.edit_list(1, "exempt_roles", r, True) for r in range(1, 11)))
        self.assertEqual(len((await self.store.settings(1)).exempt_roles), 10)
        results = await asyncio.gather(*(self.store.warn(1, 10, 99, "spam", f"message:{i}", 24, True, now=100)
                                         for i in range(20)))
        self.assertEqual(sum(wid is not None for wid, count in results), 1)
        self.assertEqual(len(await self.store.warnings(1, 10)), 1)

    async def test_invalid_update_does_not_damage_settings(self):
        with self.assertRaises(ValueError):
            await self.store.update(1, spam_limit=0)
        self.assertEqual((await self.store.settings(1)).spam_limit, 6)
        with self.assertRaises(ValueError):
            await self.store.edit_list(1, "bad_words", "", True)
        self.assertEqual((await self.store.settings(1)).bad_words, [])

    async def test_audit_failure_rolls_back_settings_and_version(self):
        await self.store.db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON settings_audit BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        await self.store.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.store.update_from_web(1, 0, 10, {"enabled": True})
        settings, version = await self.store.snapshot(1)
        self.assertFalse(settings.enabled)
        self.assertEqual(version, 0)
        await self.store.update(1, spam_limit=9)
        self.assertFalse((await self.store.settings(1)).enabled)

    async def test_delete_one_guild_only(self):
        for guild in (1, 2):
            await self.store.update(guild, enabled=True)
            await self.store.warn(guild, 10, 99, "reason", "interaction:1", 24)
        await self.store.delete_guild(1)
        self.assertFalse((await self.store.settings(1)).enabled)
        self.assertFalse(await self.store.warnings(1, 10))
        self.assertTrue((await self.store.settings(2)).enabled)
        self.assertEqual(len(await self.store.warnings(2, 10)), 1)
