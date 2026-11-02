import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot import scheduler
from bot.config import settings
from bot.db.session import init_db
from bot.handlers import capture, common, habits, ideas, money, tasks
from bot.middlewares import OwnerOnly

COMMANDS = [
    BotCommand(command="today", description="📋 Задачи на сегодня"),
    BotCommand(command="plan", description="⭐ Планёрка — 3 главные задачи"),
    BotCommand(command="tasks", description="🗂 Все задачи"),
    BotCommand(command="ideas", description="💡 Входящие идеи"),
    BotCommand(command="habits", description="🔁 Привычки"),
    BotCommand(command="report", description="📊 Отчёт по деньгам"),
    BotCommand(command="balance", description="💳 Счета"),
    BotCommand(command="undo", description="↩️ Отменить последнюю трату"),
    BotCommand(command="summary", description="☀️ Утренняя сводка"),
    BotCommand(command="review", description="🌙 Итог дня"),
    BotCommand(command="settings", description="⚙️ Настройки"),
    BotCommand(command="help", description="❓ Что я умею"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if settings.db_url.startswith("sqlite") and os.getenv("RAILWAY_ENVIRONMENT"):
        logging.warning("База SQLite на Railway стирается при каждом деплое! Подключите PostgreSQL "
                        "и задайте DATABASE_URL=${{Postgres.DATABASE_URL}}")
    await init_db()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.outer_middleware(OwnerOnly())
    dp.callback_query.outer_middleware(OwnerOnly())
    # Порядок важен: capture ловит любой текст, поэтому он последний.
    dp.include_routers(common.router, habits.router, money.router, tasks.router, ideas.router, capture.router)

    await bot.set_my_commands(COMMANDS)
    await bot.delete_webhook(drop_pending_updates=False)
    task = asyncio.create_task(scheduler.run(bot))
    logging.info("Бот запущен. AI: %s, owner: %s", settings.ai_enabled, settings.owner_id)
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
