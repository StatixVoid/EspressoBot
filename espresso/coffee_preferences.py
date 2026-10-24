"""Private usual orders, storing only a member's current menu choices."""

from .coffee_menu import character_choice, drink_choice


def _coffee_identifiers(guild_id, user_id):
    for value in (guild_id, user_id):
        if type(value) is not int or not 0 < value <= 2**63 - 1:
            raise ValueError("Use a valid server and member ID for your usual order.")


def _coffee_choices(drink, character):
    if type(drink) is not str or type(character) is not str:
        raise ValueError("Choose a drink and café companion from the beverage menu.")
    drink_choice(drink)
    character_choice(character)


class CoffeePreferenceStore:
    async def open_coffee_preferences(self):
        async with self.lock:
            try:
                await self.db.execute("BEGIN IMMEDIATE")
                await self.db.execute("""
                    CREATE TABLE IF NOT EXISTS coffee_preferences (
                        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                        drink TEXT NOT NULL, character TEXT NOT NULL,
                        PRIMARY KEY(guild_id,user_id)
                    )
                """)
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def coffee_order(self, guild_id, user_id):
        _coffee_identifiers(guild_id, user_id)
        async with self.lock:
            async with self.db.execute(
                "SELECT drink,character FROM coffee_preferences WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        # A later catalog can retire a choice without preventing the menu opening.
        try:
            _coffee_choices(row["drink"], row["character"])
        except ValueError:
            return None
        return {"drink": row["drink"], "character": row["character"]}

    async def save_coffee_order(self, guild_id, user_id, drink, character):
        _coffee_identifiers(guild_id, user_id)
        _coffee_choices(drink, character)
        async with self.lock:
            try:
                await self.db.execute("BEGIN IMMEDIATE")
                await self.db.execute("""
                    INSERT INTO coffee_preferences(guild_id,user_id,drink,character) VALUES (?,?,?,?)
                    ON CONFLICT(guild_id,user_id) DO UPDATE SET
                        drink=excluded.drink,character=excluded.character
                """, (guild_id, user_id, drink, character))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def clear_coffee_order(self, guild_id, user_id):
        _coffee_identifiers(guild_id, user_id)
        async with self.lock:
            try:
                await self.db.execute("BEGIN IMMEDIATE")
                cursor = await self.db.execute(
                    "DELETE FROM coffee_preferences WHERE guild_id=? AND user_id=?",
                    (guild_id, user_id),
                )
                removed = bool(cursor.rowcount)
                await self.db.commit()
                return removed
            except BaseException:
                await self.db.rollback()
                raise
