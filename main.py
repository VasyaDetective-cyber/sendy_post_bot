import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sendy.config import settings
from sendy.db import init_db
from sendy.handlers import admin, channels, post
from sendy.middleware import AccessMiddleware
from sendy.scheduler import setup_scheduler


async def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if settings.database_url.startswith("sqlite"):
        os.makedirs("data", exist_ok=True)

    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await init_db(engine, sm, settings.super_admin_id)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    access = AccessMiddleware(sm)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_routers(admin.router, channels.router, post.router)  # порядок важливий

    base = [BotCommand(command="channels", description="📡 Канали"),
            BotCommand(command="queue", description="⏳ Заплановані пости"),
            BotCommand(command="help", description="❓ Довідка")]
    await bot.set_my_commands(base)
    try:
        # Спрацює лише якщо власник уже бодай раз писав боту (/start).
        # Якщо ще ні — не страшно, довгий список команд він побачить після /start,
        # це налаштовується в handlers/admin.py.
        await bot.set_my_commands(base + [BotCommand(command="admins", description="👑 Адміни")],
                                  scope=BotCommandScopeChat(chat_id=settings.super_admin_id))
    except TelegramBadRequest:
        pass

    scheduler = setup_scheduler(bot, sm)
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())