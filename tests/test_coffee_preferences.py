import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from espresso.coffee_menu import CHARACTERS, DRINKS
from espresso.store import Store


class CoffeePreferenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "espresso.db")
        self.store = Store(self.path)
        await self.store.open()

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def test_usual_order_persists_and_schema_open_is_idempotent(self):
        await self.store.save_coffee_order(1, 10, "hot-chocolate", "dreamer")
        await self.store.open_coffee_preferences()
        await self.store.open_coffee_preferences()
        await self.store.close()
        self.store = Store(self.path)
        await self.store.open()
        self.assertEqual(await self.store.coffee_order(1, 10), {"drink": "hot-chocolate", "character": "dreamer"})
        async with self.store.db.execute("PRAGMA table_info(coffee_preferences)") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]
        self.assertEqual(columns, ["guild_id", "user_id", "drink", "character"])

    async def test_guild_member_isolation_overwrite_and_clear(self):
        self.assertIsNone(await self.store.coffee_order(1, 10))
        for guild, member, drink in ((1, 10, "latte"), (1, 20, "mocha"), (2, 10, "espresso")):
            await self.store.save_coffee_order(guild, member, drink, "none")
        await self.store.save_coffee_order(1, 10, "matcha-latte", "guardian")
        await self.store.save_coffee_order(1, 10, "matcha-latte", "guardian")
        self.assertEqual(await self.store.coffee_order(1, 10), {"drink": "matcha-latte", "character": "guardian"})
        self.assertEqual((await self.store.coffee_order(1, 20))["drink"], "mocha")
        self.assertEqual((await self.store.coffee_order(2, 10))["drink"], "espresso")
        self.assertFalse(await self.store.clear_coffee_order(9, 10))
        self.assertFalse(await self.store.clear_coffee_order(1, 30))
        self.assertTrue(await self.store.clear_coffee_order(1, 10))
        self.assertFalse(await self.store.clear_coffee_order(1, 10))
        self.assertIsNone(await self.store.coffee_order(1, 10))
        self.assertIsNotNone(await self.store.coffee_order(1, 20))
        self.assertIsNotNone(await self.store.coffee_order(2, 10))

    async def test_entire_catalog_surprise_and_no_companion_are_supported(self):
        for drink in ["surprise", *(item["id"] for item in DRINKS)]:
            for character in [item["id"] for item in CHARACTERS]:
                await self.store.save_coffee_order(1, 10, drink, character)
                self.assertEqual(await self.store.coffee_order(1, 10), {"drink": drink, "character": character})
        async with self.store.db.execute("SELECT COUNT(*) FROM coffee_preferences") as cursor:
            self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_invalid_ids_are_rejected_before_read_write_or_clear(self):
        await self.store.save_coffee_order(1, 10, "latte", "none")
        for invalid in (None, True, False, 0, -1, 1.0, "1", [], {}, 2**63):
            for guild, member in ((invalid, 10), (1, invalid)):
                with self.subTest(guild=guild, member=member):
                    with self.assertRaises(ValueError):
                        await self.store.coffee_order(guild, member)
                    with self.assertRaises(ValueError):
                        await self.store.save_coffee_order(guild, member, "mocha", "spark")
                    with self.assertRaises(ValueError):
                        await self.store.clear_coffee_order(guild, member)
        self.assertEqual(await self.store.coffee_order(1, 10), {"drink": "latte", "character": "none"})
        self.assertFalse(self.store.db.in_transaction)

    async def test_invalid_choices_do_not_replace_a_saved_order_or_create_rows(self):
        await self.store.save_coffee_order(1, 10, "latte", "none")
        invalid = (None, True, 1, [], {}, "", "unknown", "../.env", "https://example.com/art.png")
        for value in invalid:
            for drink, character in ((value, "spark"), ("espresso", value)):
                with self.subTest(drink=drink, character=character):
                    with self.assertRaises(ValueError):
                        await self.store.save_coffee_order(1, 10, drink, character)
                    with self.assertRaises(ValueError):
                        await self.store.save_coffee_order(2, 20, drink, character)
        self.assertEqual(await self.store.coffee_order(1, 10), {"drink": "latte", "character": "none"})
        self.assertIsNone(await self.store.coffee_order(2, 20))
        self.assertFalse(self.store.db.in_transaction)

    async def test_retired_saved_choices_load_as_no_preference(self):
        await self.store.save_coffee_order(1, 10, "latte", "none")
        for drink, character in (("retired-drink", "none"), ("latte", "retired-character")):
            await self.store.db.execute("UPDATE coffee_preferences SET drink=?,character=? WHERE guild_id=1 AND user_id=10", (drink, character))
            await self.store.db.commit()
            self.assertIsNone(await self.store.coffee_order(1, 10))
        await self.store.save_coffee_order(1, 10, "surprise", "none")
        self.assertEqual(await self.store.coffee_order(1, 10), {"drink": "surprise", "character": "none"})

    async def test_write_and_clear_failures_roll_back_and_allow_the_next_operation(self):
        await self.store.save_coffee_order(1, 10, "latte", "none")
        for action, sql in (("UPDATE", "UPDATE"), ("DELETE", "DELETE")):
            await self.store.db.execute(f"CREATE TRIGGER fail_coffee BEFORE {sql} ON coffee_preferences BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            await self.store.db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                if action == "UPDATE":
                    await self.store.save_coffee_order(1, 10, "mocha", "spark")
                else:
                    await self.store.clear_coffee_order(1, 10)
            self.assertFalse(self.store.db.in_transaction)
            self.assertEqual(await self.store.coffee_order(1, 10), {"drink": "latte", "character": "none"})
            await self.store.db.execute("DROP TRIGGER fail_coffee")
            await self.store.db.commit()
        await self.store.save_coffee_order(1, 10, "mocha", "spark")
        self.assertEqual((await self.store.coffee_order(1, 10))["drink"], "mocha")
        self.assertTrue(await self.store.clear_coffee_order(1, 10))

    async def test_concurrent_orders_share_store_lock_and_keep_one_current_row(self):
        await asyncio.gather(*(self.store.save_coffee_order(1, 10, drink, "none") for drink in ("latte", "espresso", "mocha")))
        self.assertIn((await self.store.coffee_order(1, 10))["drink"], {"latte", "espresso", "mocha"})
        async with self.store.db.execute("SELECT COUNT(*) FROM coffee_preferences") as cursor:
            self.assertEqual((await cursor.fetchone())[0], 1)
        self.assertFalse(self.store.db.in_transaction)

    async def test_old_settings_database_migrates_without_altering_settings(self):
        await self.store.close()
        self.path = str(Path(self.temp.name) / "old-espresso.db")
        old_settings = {"enabled": True, "bad_words": ["example"]}
        with closing(sqlite3.connect(self.path)) as database:
            database.execute("CREATE TABLE settings (guild_id INTEGER PRIMARY KEY,data TEXT NOT NULL)")
            database.execute("INSERT INTO settings VALUES (?,?)", (1, json.dumps(old_settings)))
            database.commit()
        self.store = Store(self.path)
        await self.store.open()
        self.assertTrue((await self.store.settings(1)).enabled)
        self.assertEqual((await self.store.settings(1)).bad_words, ["example"])
        async with self.store.db.execute("SELECT data FROM settings WHERE guild_id=1") as cursor:
            self.assertEqual(json.loads((await cursor.fetchone())[0]), old_settings)
        self.assertIsNone(await self.store.coffee_order(1, 10))
        await self.store.save_coffee_order(1, 10, "espresso", "none")
        self.assertEqual((await self.store.coffee_order(1, 10))["drink"], "espresso")

    async def test_delete_guild_removes_only_that_guilds_usual_orders(self):
        for guild, member in ((1, 10), (1, 20), (2, 10)):
            await self.store.save_coffee_order(guild, member, "latte", "none")
        await self.store.update(2, enabled=True)
        await self.store.delete_guild(1)
        self.assertIsNone(await self.store.coffee_order(1, 10))
        self.assertIsNone(await self.store.coffee_order(1, 20))
        self.assertEqual(await self.store.coffee_order(2, 10), {"drink": "latte", "character": "none"})
        self.assertTrue((await self.store.settings(2)).enabled)
        await self.store.delete_guild(1)
        self.assertIsNotNone(await self.store.coffee_order(2, 10))
