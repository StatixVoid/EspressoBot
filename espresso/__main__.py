"""Run Espresso's website and bot together, or choose either service."""

import argparse
import asyncio
import logging
import os
import signal

from aiohttp import web
import discord

from .bot import Espresso
from .config import WebConfig, load_environment
from .store import Store
from .web import Panel

LOG = logging.getLogger("espresso")


async def clear_dev_commands(token: str, guild_id: int) -> None:
    async with discord.Client(intents=discord.Intents.none()) as client:
        tree = discord.app_commands.CommandTree(client)
        await client.login(token)
        guild = discord.Object(id=guild_id)
        tree.clear_commands(guild=guild)
        await tree.sync(guild=guild)
    print("Development-server command copies removed. Global commands are unchanged.")


async def serve_panel(config: WebConfig, store: Store, bot=None) -> None:
    runner = web.AppRunner(Panel(config, store, bot).app, access_log=None)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    old_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(stop.set))
    tasks = []
    try:
        await runner.setup()
        await web.TCPSite(runner, config.host, config.port).start()
        LOG.info("Website: %s", config.base_url)
        if not config.oauth_ready:
            LOG.info("Discord website sign-in is disabled until DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET are configured.")
        if bot:
            tasks.append(asyncio.create_task(bot.connect(reconnect=True)))
        tasks.append(asyncio.create_task(stop.wait()))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    finally:
        signal.signal(signal.SIGTERM, old_term)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()


async def run_services(token: str, database: str, dev_guild: int | None,
                       config: WebConfig, web_only: bool, bot_only: bool) -> None:
    if web_only:
        store = Store(database)
        await store.open()
        try:
            await serve_panel(config, store)
        finally:
            await store.close()
    else:
        async with Espresso(database, dev_guild, website_base_url=None if bot_only else config.base_url,
                            member_screening_events=os.getenv("ENABLE_MEMBER_SCREENING_EVENTS", "false").strip().lower() == "true") as bot:
            if bot_only:
                await bot.start(token)
            else:
                await bot.login(token)
                await serve_panel(config, bot.store, bot)


def main() -> None:
    parser = argparse.ArgumentParser(description="Espresso Bot and web panel")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--web-only", action="store_true", help="Preview the website without connecting the bot")
    mode.add_argument("--bot-only", action="store_true", help="Run only the Discord bot")
    mode.add_argument("--clear-dev-commands", action="store_true", help="Remove DEV_GUILD_ID command copies, then exit")
    args = parser.parse_args()
    load_environment()
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if token == "replace_with_your_bot_token":
        token = ""
    if (args.bot_only or args.clear_dev_commands) and not token:
        raise SystemExit("Set DISCORD_TOKEN in .env or your hosting secret manager.")
    guild_value = os.environ.get("DEV_GUILD_ID", "").strip()
    try:
        dev_guild = int(guild_value) if guild_value else None
        if dev_guild is not None and dev_guild <= 0:
            raise ValueError
    except ValueError:
        raise SystemExit("DEV_GUILD_ID must be a positive server ID, or unset.") from None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("discord").setLevel(logging.WARNING)
    try:
        if args.clear_dev_commands:
            if not dev_guild:
                raise SystemExit("Set DEV_GUILD_ID to the test server whose commands should be removed.")
            asyncio.run(clear_dev_commands(token, dev_guild))
            return
        config = WebConfig.from_environment()
        web_only = args.web_only or not token
        if not token and not args.web_only:
            LOG.info("No bot token configured. Starting website preview only; edit .env to connect Discord.")
        asyncio.run(run_services(token, os.getenv("DATABASE_PATH", "data/espresso.db"),
                                 dev_guild, config, web_only, args.bot_only))
    except KeyboardInterrupt:
        pass
    except discord.LoginFailure:
        raise SystemExit("Discord rejected the bot token. Reset it in the Developer Portal and update .env.") from None
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Enable Message Content Intent in Discord. When ENABLE_MEMBER_SCREENING_EVENTS=true, enable Server Members Intent too.") from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        raise SystemExit(f"Espresso stopped ({type(exc).__name__}). Check network access, web port, database path, and Discord permissions.") from None


if __name__ == "__main__":
    main()
