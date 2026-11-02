"""Утренняя сводка и вечерний итог."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Idea, Task, UserSettings
from bot.services import repo
from bot.ui import MONTHS, WEEKDAYS, kb, money, short, task_line


def evening_kb(tasks: list[Task]):
    rows = [[(f"✅ {short(t.title, 24)}", f"tk:done:{t.id}:eve"), ("➡️ завтра", f"tk:mv:{t.id}:1:eve")]
            for t in tasks[:10]]
    if tasks:
        rows.append([("➡️ Всё оставшееся на завтра", "ev:all")])
    return kb(rows)


async def money_block(s: AsyncSession) -> list[str]:
    today = repo.now_local().date()
    yday = today - timedelta(days=1)
    month_start = today.replace(day=1)
    y_spent = await repo.spent(s, yday, yday, today)
    m_spent = await repo.spent(s, month_start, today, today)
    lines = [f"💸 Вчера потрачено: <b>{money(y_spent)}</b> · за месяц: <b>{money(m_spent)}</b>"]
    st = await s.get(UserSettings, 1)
    if st and st.monthly_budget:
        left = Decimal(st.monthly_budget) - m_spent
        days_left = ((month_start + timedelta(days=32)).replace(day=1) - today).days
        if left > 0:
            lines.append(f"📊 Осталось {money(left)} — по <b>{money(left / days_left)}</b> в день")
        else:
            lines.append(f"🚨 Бюджет месяца превышен на {money(-left)}")
    return lines


async def morning(s: AsyncSession) -> tuple[str, object]:
    today = repo.now_local().date()
    tasks = await repo.today_tasks(s, today)
    habits = await repo.habit_states(s, today)
    lines = [f"☀️ <b>Доброе утро!</b> {WEEKDAYS[today.weekday()]}, {today.day} {MONTHS[today.month - 1]}", ""]
    if tasks:
        overdue = sum(1 for t in tasks if t.due_date and t.due_date < today)
        lines.append(f"📋 <b>Задачи на сегодня ({len(tasks)})</b>" + (f" · ⚠️ просрочено: {overdue}" if overdue else ""))
        lines += [task_line(t, today) for t in tasks[:10]]
        if len(tasks) > 10:
            lines.append(f"…и ещё {len(tasks) - 10}")
    else:
        lines.append("📋 Задач на сегодня пока нет.")
    if habits:
        lines += ["", "✅ <b>Привычки:</b> " + ", ".join(
            escape(h.habit.name) + (f" 🔥{h.streak}" if h.streak > 1 else "") for h in habits)]
    lines += [""] + await money_block(s)
    inbox = await s.scalar(select(Idea).where(Idea.archived.is_(False)).order_by(Idea.id.desc()).limit(1))
    if inbox:
        lines += ["", f"💡 Последняя идея: <i>{escape(short(inbox.text, 80))}</i>"]
    lines += ["", "Выберите 3 главные задачи дня 👇"]
    return "\n".join(lines), kb([[("⭐ Планёрка", "tk:plan"), ("📋 Сегодня", "tk:today")],
                                  [("✅ Привычки", "hb:list")]])


async def evening(s: AsyncSession) -> tuple[str, object]:
    today = repo.now_local().date()
    done = await repo.done_today(s, today)
    open_tasks = [t for t in await repo.today_tasks(s, today) if t.due_date is None or t.due_date <= today]
    habits = await repo.habit_states(s, today)
    r = await repo.report(s, today, today, today)
    lines = ["🌙 <b>Итог дня</b>", ""]
    if done:
        lines.append(f"✅ Сделано: <b>{len(done)}</b>")
        lines += [f"  • {escape(t.title)}" for t in done[:10]]
    else:
        lines.append("✅ Сегодня задач не закрыто.")
    if open_tasks:
        lines += ["", f"⏳ Осталось ({len(open_tasks)}) — отметьте или перенесите:"]
        lines += [task_line(t, today, with_cmd=False) for t in open_tasks[:10]]
    if habits:
        n = sum(1 for h in habits if h.done_today)
        lines += ["", f"🔁 Привычки: {n}/{len(habits)}"]
        missed = [escape(h.habit.name) for h in habits if not h.done_today and h.habit.per_week >= 7]
        if missed:
            lines.append("Ещё не отмечены: " + ", ".join(missed))
    lines += ["", f"💸 Потрачено сегодня: <b>{money(r.expense)}</b>"
              + (f" · 💰 доход: {money(r.income)}" if r.income else "")]
    lines += ["", "<i>Что важного случилось сегодня? Напишите — сохраню во входящие.</i>"]
    markup = evening_kb(open_tasks)
    if habits and any(not h.done_today for h in habits):
        markup.inline_keyboard.append(kb([[("✅ Отметить привычки", "hb:list")]]).inline_keyboard[0])
    return "\n".join(lines), markup
