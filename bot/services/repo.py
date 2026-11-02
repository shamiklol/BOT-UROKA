"""Запросы к базе, которые нужны в нескольких местах."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import Account, Category, CategoryRule, Habit, HabitLog, Task, Transaction
from bot.services.currency import get_rate


def now_local() -> datetime:
    return datetime.now(settings.zone).replace(tzinfo=None)


# --- финансы -------------------------------------------------------------------

async def categories(s: AsyncSession, kind: str) -> list[Category]:
    return list(await s.scalars(select(Category).where(Category.kind == kind).order_by(Category.id)))


async def category_by_name(s: AsyncSession, name: str | None, kind: str) -> Category | None:
    if not name:
        return None
    # Сравниваем в Python: lower() в SQLite не понимает кириллицу.
    return next((c for c in await categories(s, kind) if c.name.casefold() == name.casefold()), None)


async def learned_rules(s: AsyncSession) -> dict[str, str]:
    rows = await s.execute(select(CategoryRule.keyword, Category.name).join(Category))
    return {k: n for k, n in rows.all()}


async def learn(s: AsyncSession, keyword: str, category_id: int) -> None:
    rule = await s.scalar(select(CategoryRule).where(CategoryRule.keyword == keyword))
    if rule:
        rule.category_id = category_id
    else:
        s.add(CategoryRule(keyword=keyword, category_id=category_id))


async def accounts(s: AsyncSession) -> list[Account]:
    return list(await s.scalars(select(Account).order_by(Account.id)))


async def default_account(s: AsyncSession, currency: str) -> Account:
    accs = await accounts(s)
    for a in accs:
        if a.currency == currency and (a.is_default or currency != "UZS"):
            return a
    return next((a for a in accs if a.currency == currency), accs[0])


async def balances(s: AsyncSession) -> list[tuple[Account, Decimal]]:
    result = []
    for acc in await accounts(s):
        inc = await s.scalar(select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.account_id == acc.id, Transaction.kind == "income")) or 0
        exp = await s.scalar(select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.account_id == acc.id, Transaction.kind == "expense")) or 0
        result.append((acc, Decimal(acc.initial_balance) + Decimal(inc) - Decimal(exp)))
    return result


@dataclass
class Report:
    start: date
    end: date
    expense: Decimal
    income: Decimal
    by_category: list[tuple[str, Decimal]]
    count: int


async def report(s: AsyncSession, start: date, end: date, today: date) -> Report:
    """Итоги за период [start, end] в сумах (доллары по сегодняшнему курсу)."""
    rows = list(await s.scalars(select(Transaction).where(Transaction.day >= start, Transaction.day <= end)))
    usd = await get_rate(s, "USD", today) if any(t.currency == "USD" for t in rows) else Decimal(1)
    exp = inc = Decimal(0)
    cats: dict[str, Decimal] = defaultdict(Decimal)
    for t in rows:
        v = Decimal(t.amount) * (usd if t.currency == "USD" else 1)
        if t.kind == "expense":
            exp += v
            cats[t.category.label if t.category else "📦 Без категории"] += v
        else:
            inc += v
    return Report(start, end, exp, inc, sorted(cats.items(), key=lambda x: -x[1]), len(rows))


async def spent(s: AsyncSession, start: date, end: date, today: date) -> Decimal:
    return (await report(s, start, end, today)).expense


# --- задачи --------------------------------------------------------------------

async def today_tasks(s: AsyncSession, today: date) -> list[Task]:
    """Открытые задачи на сегодня и просроченные: сначала главные, потом по приоритету."""
    q = select(Task).where(Task.done.is_(False), or_(Task.due_date <= today, Task.is_top.is_(True)))
    tasks = list(await s.scalars(q))
    tasks.sort(key=lambda t: (not t.is_top, t.priority, t.due_time or datetime.max.time(), t.id))
    return tasks


async def upcoming_tasks(s: AsyncSession, today: date, days: int = 14) -> list[Task]:
    q = select(Task).where(Task.done.is_(False), Task.is_top.is_(False),
                           and_(Task.due_date > today, Task.due_date <= today + timedelta(days=days)))
    return list(await s.scalars(q.order_by(Task.due_date, Task.due_time, Task.id)))


async def someday_tasks(s: AsyncSession) -> list[Task]:
    q = select(Task).where(Task.done.is_(False), Task.due_date.is_(None), Task.is_top.is_(False))
    return list(await s.scalars(q.order_by(Task.priority, Task.id)))


async def done_today(s: AsyncSession, today: date) -> list[Task]:
    start = datetime.combine(today, datetime.min.time())
    return list(await s.scalars(select(Task).where(Task.done.is_(True), Task.done_at >= start)))


# --- привычки ------------------------------------------------------------------

@dataclass
class HabitState:
    habit: Habit
    done_today: bool
    streak: int
    week_done: int


async def habit_states(s: AsyncSession, today: date) -> list[HabitState]:
    habits = list(await s.scalars(select(Habit).where(Habit.active.is_(True)).order_by(Habit.id)))
    week_start = today - timedelta(days=today.weekday())
    states = []
    for h in habits:
        days = set(await s.scalars(select(HabitLog.day).where(HabitLog.habit_id == h.id)))
        streak = 0
        d = today if today in days else today - timedelta(days=1)
        if h.per_week >= 7:
            while d in days:
                streak += 1
                d -= timedelta(days=1)
        else:  # для «N раз в неделю» серия — недели подряд с выполненной нормой
            ws = week_start if sum(1 for x in days if x >= week_start) >= h.per_week else week_start - timedelta(days=7)
            while sum(1 for x in days if ws <= x < ws + timedelta(days=7)) >= h.per_week:
                streak += 1
                ws -= timedelta(days=7)
        week_done = sum(1 for x in days if week_start <= x <= today)
        states.append(HabitState(h, today in days, streak, week_done))
    return states
