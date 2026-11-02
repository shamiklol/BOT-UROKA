"""Фоновый цикл раз в 30 секунд: сводки утром/вечером, напоминания о задачах и привычках.

Всё состояние (что уже отправлено) хранится в базе, поэтому перезапуск на Railway
ничего не теряет и не шлёт дубли.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot
from sqlalchemy import delete, select

from bot.config import settings
from bot.db.models import Draft, Habit, HabitLog, Task, UserSettings
from bot.db.session import Session
from bot.services import repo, summaries
from bot.ui import hm, kb

log = logging.getLogger(__name__)
WINDOW = timedelta(hours=3)  # если бот был выключен, сводку отправим, только пока не прошло 3 часа


def _due(now: datetime, at) -> bool:
    start = datetime.combine(now.date(), at)
    return start <= now < start + WINDOW


async def tick(bot: Bot) -> None:
    chat = settings.owner_id
    if chat is None:
        return
    now = repo.now_local()
    today = now.date()
    async with Session() as s:
        st = await s.get(UserSettings, 1)
        if st.last_morning != today and _due(now, st.morning_time):
            st.last_morning = today
            await s.commit()
            text, markup = await summaries.morning(s)
            await bot.send_message(chat, text, reply_markup=markup)

        if st.last_evening != today and _due(now, st.evening_time):
            st.last_evening = today
            await s.commit()
            text, markup = await summaries.evening(s)
            await bot.send_message(chat, text, reply_markup=markup)

        tasks = await s.scalars(select(Task).where(
            Task.done.is_(False), Task.reminded.is_(False), Task.due_date == today,
            Task.due_time.is_not(None), Task.due_time <= now.time()))
        for t in list(tasks):
            t.reminded = True
            await s.commit()
            await bot.send_message(chat, f"⏰ <b>{escape(t.title)}</b>" + (f" — {hm(t.due_time)}"),
                                   reply_markup=kb([[("✅ Готово", f"tk:done:{t.id}:msg"),
                                                     ("⏱ +1 час", f"tk:later:{t.id}"),
                                                     ("➡️ Завтра", f"tk:mv:{t.id}:1:msg")]]))

        habits = await s.scalars(select(Habit).where(
            Habit.active.is_(True), Habit.remind_time.is_not(None), Habit.remind_time <= now.time()))
        for h in list(habits):
            if h.last_reminded == today:
                continue
            h.last_reminded = today
            await s.commit()
            done = await s.scalar(select(HabitLog).where(HabitLog.habit_id == h.id, HabitLog.day == today))
            if done or now - datetime.combine(today, h.remind_time) > WINDOW:
                continue
            await bot.send_message(chat, f"🔁 Время для привычки: <b>{escape(h.name)}</b>",
                                   reply_markup=kb([[("✅ Сделал", f"hb:r:{h.id}"), ("Пропустить", "hb:skip:0")]]))

        # Чистим брошенные черновики старше суток.
        await s.execute(delete(Draft).where(Draft.created_at < now - timedelta(days=1)))
        await s.commit()


async def run(bot: Bot) -> None:
    while True:
        try:
            await tick(bot)
        except Exception:
            log.exception("Ошибка в фоновом цикле")
        await asyncio.sleep(30)
