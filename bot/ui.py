"""Тексты и клавиатуры."""
from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from html import escape

from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup

from bot.db.models import Task

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
          "октября", "ноября", "декабря"]
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
PRIORITY = {"A": "🔴", "B": "🟡", "C": "⚪"}


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[Btn(text=t, callback_data=d) for t, d in row] for row in rows if row])


def money(amount: Decimal | float | int, currency: str = "UZS") -> str:
    a = Decimal(amount)
    if currency == "USD":
        return f"${a:,.2f}".replace(",", " ").replace(".00", "")
    return f"{a:,.0f}".replace(",", " ") + " сум"


def human_date(d: date | None, today: date) -> str:
    if d is None:
        return "без даты"
    delta = (d - today).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    if delta == -1:
        return "вчера"
    if 1 < delta < 7:
        return f"{WD_SHORT[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"
    return f"{d.day} {MONTHS[d.month - 1]}" + (f" {d.year}" if d.year != today.year else "")


def hm(t: time | None) -> str:
    return t.strftime("%H:%M") if t else ""


def task_line(t: Task, today: date, with_date: bool = True, with_cmd: bool = True) -> str:
    parts = [("⭐ " if t.is_top else "") + PRIORITY.get(t.priority, ""), escape(t.title)]
    when = []
    if with_date and t.due_date and t.due_date != today:
        when.append(("⚠️ " if t.due_date < today else "") + human_date(t.due_date, today))
    if t.due_time:
        when.append(hm(t.due_time))
    line = " ".join(p for p in parts if p)
    if when:
        line += f" — <i>{', '.join(when)}</i>"
    if with_cmd:
        line += f"  /t{t.id}"
    return line


def bar(part: Decimal, total: Decimal, width: int = 10) -> str:
    if total <= 0:
        return ""
    n = round(float(part / total) * width)
    return "▓" * n + "░" * (width - n)


def short(text: str, n: int = 28) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"
