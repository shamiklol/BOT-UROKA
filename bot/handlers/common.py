"""Старт, помощь, настройки, сводки по команде."""
from __future__ import annotations

import re
from datetime import time
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import settings
from bot.db.models import UserSettings
from bot.db.session import Session
from bot.services import repo, summaries
from bot.services.parser import parse_money
from bot.ui import hm, money

router = Router(name="common")

HELP = """<b>Просто пишите боту — он сам поймёт, что это:</b>

💸 <b>Трата</b>: <i>кофе 25к</i>, <i>такси 30 000</i>, <i>вчера обед 85к</i>, <i>$20 подписка</i>
💰 <b>Доход</b>: <i>+5 млн зарплата</i>
📋 <b>Задача</b>: <i>завтра в 15:00 созвон с Азизом</i>, <i>в пятницу отправить отчёт</i>, <i>срочно оплатить интернет</i>
💡 <b>Идея</b>: всё остальное, или начните со слова «идея»
🎙 Голосовые — тоже можно{voice}

Перед сохранением траты и задачи бот покажет карточку — можно поправить категорию, счёт, дату.

<b>Команды</b>
/today — задачи на сегодня
/plan — планёрка: выбрать 3 главные задачи
/tasks — все задачи
/ideas — входящие идеи
/habits — привычки · /habit Зарядка 07:30
/report — отчёт по деньгам
/balance — счета и остатки
/undo — отменить последнюю трату
/summary — утренняя сводка сейчас
/review — вечерний итог сейчас
/settings — время сводок и бюджет"""


@router.message(CommandStart())
async def start(message: Message) -> None:
    name = escape(message.from_user.first_name or "")
    await message.answer(f"👋 Привет, {name}! Я ваш личный помощник: деньги, задачи, идеи и привычки.\n\n"
                         + HELP.format(voice="" if settings.openai_api_key else " (нужен ключ OPENAI_API_KEY)"))


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP.format(voice="" if settings.openai_api_key else " (нужен ключ OPENAI_API_KEY)"))


@router.message(Command("cancel"), StateFilter("*"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено")


async def settings_text() -> str:
    async with Session() as s:
        st = await s.get(UserSettings, 1)
    budget = money(st.monthly_budget) if st.monthly_budget else "не задан"
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"☀️ Утренняя сводка: <b>{hm(st.morning_time)}</b> — /morning 08:00\n"
        f"🌙 Вечерний итог: <b>{hm(st.evening_time)}</b> — /evening 21:30\n"
        f"📊 Бюджет на месяц: <b>{budget}</b> — /budget 6 млн (или /budget 0 — убрать)\n"
        f"🕐 Часовой пояс: {settings.tz}\n"
        f"🤖 ИИ-разбор: {'включён' if settings.ai_enabled else 'выключен (нет ANTHROPIC_API_KEY)'}\n"
        f"🎙 Голосовые: {'включены' if settings.openai_api_key else 'выключены (нет OPENAI_API_KEY)'}"
    )


@router.message(Command("settings"))
async def settings_cmd(message: Message) -> None:
    await message.answer(await settings_text())


def _time_arg(text: str) -> time | None:
    m = re.search(r"\b([01]?\d|2[0-3])[:.]?([0-5]\d)?\b", text.split(maxsplit=1)[1] if " " in text else "")
    return time(int(m.group(1)), int(m.group(2) or 0)) if m else None


@router.message(Command("morning", "evening"))
async def set_time(message: Message) -> None:
    t = _time_arg(message.text or "")
    if t is None:
        await message.answer("Укажите время, например: <code>/morning 07:30</code>")
        return
    is_morning = message.text.lstrip("/").startswith("morning")
    async with Session() as s:
        st = await s.get(UserSettings, 1)
        if is_morning:
            st.morning_time = t
        else:
            st.evening_time = t
        await s.commit()
    await message.answer(f"✅ {'Утренняя сводка' if is_morning else 'Вечерний итог'} теперь в {hm(t)}")


@router.message(Command("budget"))
async def set_budget(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажите бюджет на месяц: <code>/budget 6 млн</code>")
        return
    async with Session() as s:
        st = await s.get(UserSettings, 1)
        if args[1].strip() in ("0", "нет", "off"):
            st.monthly_budget = None
            await s.commit()
            await message.answer("Бюджет убран")
            return
        parsed = parse_money(args[1], repo.now_local().date())
        if not parsed:
            await message.answer("Не понял сумму. Пример: <code>/budget 6 млн</code>")
            return
        st.monthly_budget = parsed.amount
        await s.commit()
    await message.answer(f"✅ Бюджет на месяц: {money(parsed.amount)}. "
                         "Буду показывать, сколько можно тратить в день.")


@router.message(Command("summary"))
async def summary_now(message: Message) -> None:
    async with Session() as s:
        text, markup = await summaries.morning(s)
    await message.answer(text, reply_markup=markup)


@router.message(Command("review"))
async def review_now(message: Message) -> None:
    async with Session() as s:
        text, markup = await summaries.evening(s)
    await message.answer(text, reply_markup=markup)
