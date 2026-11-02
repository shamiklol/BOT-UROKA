"""Идеи: входящие, превращение в задачу, архив."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.db.models import Idea, Task
from bot.db.session import Session
from bot.services import repo
from bot.services.parser import parse_task
from bot.ui import human_date, kb, short, task_line

router = Router(name="ideas")
KIND_ICON = {"idea": "💡", "note": "📝", "purchase": "🛒"}


def saved_markup(idea_id: int):
    return kb([[("📋 В задачи", f"i:task:{idea_id}"), ("🗄 В архив", f"i:arch:{idea_id}"),
                ("🗑", f"i:del:{idea_id}")]])


def saved_text(idea: Idea) -> str:
    tags = " ".join(f"#{escape(t)}" for t in idea.tags or [])
    return f"{KIND_ICON.get(idea.kind, '💡')} Сохранил во входящие" + (f"\n{tags}" if tags else "") + f"  /i{idea.id}"


async def ideas_view() -> tuple[str, object]:
    async with Session() as s:
        ideas = list(await s.scalars(select(Idea).where(Idea.archived.is_(False)).order_by(Idea.id.desc()).limit(30)))
    if not ideas:
        return ("💡 <b>Входящие пусты</b>\n\nПросто пишите боту мысли, идеи, ссылки — "
                "всё, что не трата и не задача, попадёт сюда."), None
    today = repo.now_local().date()
    lines = [f"💡 <b>Входящие · {len(ideas)}</b>", ""]
    for i in ideas:
        tags = " ".join(f"#{escape(t)}" for t in (i.tags or [])[:3])
        lines.append(f"{KIND_ICON.get(i.kind, '💡')} {escape(short(i.text, 90))} "
                     f"<i>{human_date(i.created_at.date(), today)}</i> {tags}  /i{i.id}")
    lines += ["", "<i>Откройте /i… чтобы превратить идею в задачу или убрать в архив</i>"]
    return "\n".join(lines), None


@router.message(Command("ideas", "inbox"))
async def ideas_cmd(message: Message) -> None:
    text, markup = await ideas_view()
    await message.answer(text, reply_markup=markup)


@router.message(F.text.regexp(r"^/i(\d+)(@\w+)?$").as_("m"))
async def idea_open(message: Message, m) -> None:
    async with Session() as s:
        idea = await s.get(Idea, int(m.group(1)))
    if not idea:
        await message.answer("Идея не найдена")
        return
    tags = " ".join(f"#{escape(t)}" for t in idea.tags or [])
    status = " · 🗄 в архиве" if idea.archived else ""
    await message.answer(f"{KIND_ICON.get(idea.kind, '💡')} {escape(idea.text)}\n\n{tags}{status}",
                         reply_markup=saved_markup(idea.id))


@router.callback_query(F.data.startswith("i:"))
async def idea_cb(cb: CallbackQuery) -> None:
    _, action, idea_id = cb.data.split(":")
    async with Session() as s:
        idea = await s.get(Idea, int(idea_id))
        if idea is None:
            return await cb.answer("Идея не найдена", show_alert=True)
        if action == "del":
            await s.delete(idea)
            await s.commit()
            await cb.message.edit_text("🗑 Удалено")
        elif action == "arch":
            idea.archived = True
            await s.commit()
            await cb.message.edit_text(f"🗄 В архиве: {escape(short(idea.text, 60))}")
        elif action == "task":
            now = repo.now_local()
            p = parse_task(idea.text, now)
            task = Task(title=p.title, due_date=p.due_date, due_time=p.due_time, priority=p.priority,
                        created_at=now)
            s.add(task)
            idea.archived = True
            await s.commit()
            await cb.message.edit_text(f"📋 Перенёс в задачи\n{task_line(task, now.date())}")
    await cb.answer()
