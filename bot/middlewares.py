"""Бот личный: отвечает только владельцу (OWNER_ID)."""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import settings


class OwnerOnly(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return None
        if settings.owner_id is None:
            # Первый запуск: подсказываем ID, чтобы вписать его в настройки Railway.
            if isinstance(event, Message):
                await event.answer(
                    f"🔐 Ваш Telegram ID: <code>{user.id}</code>\n\n"
                    f"Добавьте переменную <code>OWNER_ID={user.id}</code> в Railway — "
                    "после перезапуска бот будет отвечать только вам."
                )
            return None
        if user.id != settings.owner_id:
            if isinstance(event, CallbackQuery):
                await event.answer("Это личный бот", show_alert=True)
            return None
        return await handler(event, data)
