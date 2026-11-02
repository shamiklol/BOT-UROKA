"""Привычки: отметки одной кнопкой, серии, напоминания."""
from __future__ import annotations

import re
from datetime import time
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, select

from bot.db.models import Habit, HabitLog
from bot.db.session import Session
from bot.services import repo
from bot.ui import hm, kb

router = Router(name="habits")
FREQ = {7: "каждый день", 5: "5 раз в неделю", 3: "3 раза в неделю", 1: "раз в неделю"}


class AddHabit(StatesGroup):
    name = State()
    freq = State()
    time = State()


def _parse_time(text: str) -> time | None:
    m = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", text)
    return time(int(m.group(1)), int(m.group(2))) if m else None


async def habits_view() -> tuple[str, object]:
    async with Session() as s:
        states = await repo.habit_states(s, repo.now_local().date())
    if not states:
        return ("🔁 <b>Привычек пока нет</b>\n\nДобавьте кнопкой ниже или командой:\n"
                "<code>/habit Зарядка 07:30</code>"), kb([[("➕ Добавить привычку", "hb:add")]])
    done = sum(1 for st in states if st.done_today)
    lines = [f"🔁 <b>Привычки сегодня</b> · {done}/{len(states)}", ""]
    rows = []
    for st in states:
        h = st.habit
        info = []
        if st.streak:
            info.append(f"🔥{st.streak}" + (" нед." if h.per_week < 7 else ""))
        if h.per_week < 7:
            info.append(f"неделя {st.week_done}/{h.per_week}")
        if h.remind_time:
            info.append(f"⏰ {hm(h.remind_time)}")
        lines.append(f"{'✅' if st.done_today else '⬜'} {escape(h.name)}" + (f" · {' · '.join(info)}" if info else ""))
        rows.append([(f"{'✅' if st.done_today else '⬜'} {h.name}", f"hb:t:{h.id}")])
    rows.append([("➕ Добавить", "hb:add"), ("⚙️ Удалить", "hb:manage")])
    return "\n".join(lines), kb(rows)


@router.message(Command("habits"))
async def habits_cmd(message: Message) -> None:
    text, markup = await habits_view()
    await message.answer(text, reply_markup=markup)


@router.message(Command("habit"))
async def habit_quick_add(message: Message, state: FSMContext) -> None:
    """/habit Зарядка 07:30 5 — название, время напоминания и раз в неделю (необязательно)."""
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await state.set_state(AddHabit.name)
        await message.answer("Как назвать привычку? Например: <i>Зарядка</i>, <i>Чтение 20 минут</i>")
        return
    raw = args[1]
    t = _parse_time(raw)
    raw = re.sub(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", "", raw)
    m = re.search(r"\b([1-7])\s*(?:раз\w*)?\s*(?:в\s*нед\w*)?\s*$", raw)
    per_week = int(m.group(1)) if m else 7
    if m:
        raw = raw[: m.start()]
    name = raw.strip(" ,-") or "Привычка"
    async with Session() as s:
        s.add(Habit(name=name, per_week=per_week, remind_time=t, created_at=repo.now_local()))
        await s.commit()
    await message.answer(f"✅ Привычка «{escape(name)}» — {FREQ.get(per_week, f'{per_week} раз в неделю')}"
                         + (f", напомню в {hm(t)}" if t else ""))


@router.message(StateFilter(AddHabit.name), F.text)
async def add_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip()[:128])
    await state.set_state(AddHabit.freq)
    await message.answer("Как часто?", reply_markup=kb([
        [("Каждый день", "hb:f:7"), ("5 раз в неделю", "hb:f:5")],
        [("3 раза в неделю", "hb:f:3"), ("Раз в неделю", "hb:f:1")],
    ]))


@router.callback_query(StateFilter(AddHabit.freq), F.data.startswith("hb:f:"))
async def add_freq(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(per_week=int(cb.data.split(":")[2]))
    await state.set_state(AddHabit.time)
    await cb.message.edit_text("Во сколько напоминать? Напишите время, например <code>07:30</code>",
                               reply_markup=kb([[("Без напоминания", "hb:nt")]]))
    await cb.answer()


async def _finish(state: FSMContext, t: time | None) -> str:
    data = await state.get_data()
    await state.clear()
    async with Session() as s:
        s.add(Habit(name=data["name"], per_week=data.get("per_week", 7), remind_time=t,
                    created_at=repo.now_local()))
        await s.commit()
    return (f"✅ Привычка «{escape(data['name'])}» добавлена — {FREQ.get(data.get('per_week', 7))}"
            + (f", напомню в {hm(t)}" if t else "") + "\n\nОтмечайте в /habits")


@router.callback_query(StateFilter(AddHabit.time), F.data == "hb:nt")
async def add_no_time(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.edit_text(await _finish(state, None))
    await cb.answer()


@router.message(StateFilter(AddHabit.time), F.text)
async def add_time(message: Message, state: FSMContext) -> None:
    t = _parse_time(message.text)
    if t is None:
        await message.answer("Не понял время. Напишите как <code>07:30</code> или нажмите «Без напоминания».")
        return
    await message.answer(await _finish(state, t))


@router.callback_query(F.data.startswith("hb:"))
async def habit_cb(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    action = parts[1]
    today = repo.now_local().date()
    if action == "add":
        await state.set_state(AddHabit.name)
        await cb.message.answer("Как назвать привычку? Например: <i>Зарядка</i>, <i>Чтение 20 минут</i>")
        return await cb.answer()
    if action == "manage":
        async with Session() as s:
            habits = list(await s.scalars(select(Habit).where(Habit.active.is_(True)).order_by(Habit.id)))
        rows = [[(f"🗑 {h.name}", f"hb:del:{h.id}")] for h in habits] + [[("← Назад", "hb:list")]]
        await cb.message.edit_text("Какую привычку удалить?", reply_markup=kb(rows))
        return await cb.answer()
    if action in ("t", "r", "del"):
        async with Session() as s:
            h = await s.get(Habit, int(parts[2]))
            if h is None:
                return await cb.answer("Привычка не найдена", show_alert=True)
            if action == "del":
                h.active = False
            else:
                log = await s.scalar(select(HabitLog).where(HabitLog.habit_id == h.id, HabitLog.day == today))
                if log and action == "t":
                    await s.execute(delete(HabitLog).where(HabitLog.id == log.id))
                elif not log:
                    s.add(HabitLog(habit_id=h.id, day=today))
            await s.commit()
            if action == "r":  # из напоминания
                st = next((x for x in await repo.habit_states(s, today) if x.habit.id == h.id), None)
                streak = f" — серия 🔥{st.streak}" if st and st.streak > 1 else ""
                await cb.message.edit_text(f"✅ {escape(h.name)}{streak}")
                return await cb.answer("Отлично!")
    if action == "skip":
        await cb.message.edit_reply_markup(reply_markup=None)
        return await cb.answer("Ок, в другой раз")
    text, markup = await habits_view()
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()
