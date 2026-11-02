"""Любое сообщение без команды: понять, что это, и показать карточку."""
from __future__ import annotations

import io
import logging
from datetime import date, time
from decimal import Decimal
from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.db.models import Draft, Idea
from bot.db.session import Session
from bot.handlers.ideas import saved_markup, saved_text
from bot.handlers.money import new_money_draft, render_money
from bot.handlers.tasks import new_task_draft, render_task_draft
from bot.services import repo
from bot.services.ai import classify_ai
from bot.services.parser import classify, parse_money, parse_task
from bot.services.voice import transcribe

log = logging.getLogger(__name__)
router = Router(name="capture")


async def handle_text(message: Message, text: str) -> None:
    now = repo.now_local()
    async with Session() as s:
        learned = await repo.learned_rules(s)
        c = classify(text, now, learned)

        # Правила не уверены → спрашиваем Claude (если ключ задан).
        if not c.confident and settings.ai_enabled:
            await message.bot.send_chat_action(message.chat.id, "typing")
            exp = [x.name for x in await repo.categories(s, "expense")]
            inc = [x.name for x in await repo.categories(s, "income")]
            ai = await classify_ai(text, now.date(), exp, inc)
            if ai:
                if ai.type in ("expense", "income") and ai.amount:
                    draft = await new_money_draft(
                        s, text=text, kind=ai.type, amount=Decimal(str(ai.amount)), currency=ai.currency or "UZS",
                        note=ai.note or "", day=now.date(), category_name=ai.category)
                    t, m = await render_money(s, draft)
                    await message.answer(t, reply_markup=m)
                    return
                if ai.type == "task":
                    due_d = _safe(date.fromisoformat, ai.due_date)
                    due_t = _safe(time.fromisoformat, ai.due_time)
                    draft = await new_task_draft(s, text=text, title=ai.title or text, due_date=due_d,
                                                 due_time=due_t, priority=ai.priority or "B")
                    t, m = render_task_draft(draft)
                    await message.answer(t, reply_markup=m)
                    return
                await save_idea(message, s, text, ai.idea_kind or "idea", ai.tags or c.tags)
                return

        if c.type == "money":
            p = c.money
            draft = await new_money_draft(s, text=text, kind=p.kind, amount=p.amount, currency=p.currency,
                                          note=p.note, day=p.day, category_name=p.category)
            t, m = await render_money(s, draft)
            await message.answer(t, reply_markup=m)
        elif c.type == "task":
            p = c.task
            draft = await new_task_draft(s, text=text, title=p.title, due_date=p.due_date,
                                         due_time=p.due_time, priority=p.priority)
            t, m = render_task_draft(draft)
            await message.answer(t, reply_markup=m)
        else:
            await save_idea(message, s, c.idea_text or text, "idea", c.tags)


def _safe(fn, value):
    try:
        return fn(value) if value else None
    except ValueError:
        return None


async def save_idea(message: Message, s, text: str, kind: str, tags: list[str]) -> None:
    idea = Idea(text=text, kind=kind, tags=[t.lower() for t in tags][:5], created_at=repo.now_local())
    s.add(idea)
    await s.commit()
    await message.answer(saved_text(idea), reply_markup=saved_markup(idea.id))


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message) -> None:
    await handle_text(message, message.text)


@router.message(F.caption & (F.photo | F.document))
async def on_caption(message: Message) -> None:
    await handle_text(message, message.caption)


@router.message(F.voice | F.audio)
async def on_voice(message: Message, bot: Bot) -> None:
    if not settings.openai_api_key:
        await message.answer("🎙 Чтобы я понимал голосовые, добавьте переменную OPENAI_API_KEY в Railway.")
        return
    media = message.voice or message.audio
    await bot.send_chat_action(message.chat.id, "typing")
    buf = io.BytesIO()
    await bot.download(media, destination=buf)
    text = await transcribe(buf.getvalue())
    if not text:
        await message.answer("🎙 Не получилось разобрать голосовое, попробуйте ещё раз или напишите текстом.")
        return
    await message.answer(f"🎙 <i>{escape(text)}</i>")
    await handle_text(message, text)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Пока я понимаю текст и голосовые. /help — что я умею.")


# --- «это не то»: перевод черновика в другой тип ---------------------------------

@router.callback_query(F.data.startswith("d:"))
async def convert(cb: CallbackQuery) -> None:
    _, target, draft_id = cb.data.split(":")
    async with Session() as s:
        draft = await s.get(Draft, int(draft_id))
        if draft is None:
            await cb.answer("Черновик устарел", show_alert=True)
            return
        text = draft.data.get("text", "")
        now = repo.now_local()
        await s.delete(draft)
        await s.commit()

        if target == "idea":
            idea = Idea(text=text, kind="idea", tags=[], created_at=now)
            s.add(idea)
            await s.commit()
            await cb.message.edit_text(saved_text(idea), reply_markup=saved_markup(idea.id))
        elif target == "task":
            p = parse_task(text, now)
            new = await new_task_draft(s, text=text, title=p.title, due_date=p.due_date,
                                       due_time=p.due_time, priority=p.priority)
            t, m = render_task_draft(new)
            await cb.message.edit_text(t, reply_markup=m)
        elif target == "money":
            p = parse_money(text, now.date(), await repo.learned_rules(s))
            if p is None:
                # Возвращаем черновик задачи — суммы в тексте нет.
                tp = parse_task(text, now)
                back = await new_task_draft(s, text=text, title=tp.title, due_date=tp.due_date,
                                            due_time=tp.due_time, priority=tp.priority)
                t, m = render_task_draft(back)
                await cb.message.edit_text(t, reply_markup=m)
                await cb.answer("Не нашёл сумму в тексте", show_alert=True)
                return
            new = await new_money_draft(s, text=text, kind=p.kind, amount=p.amount, currency=p.currency,
                                        note=p.note or parse_task(text, now).title, day=p.day,
                                        category_name=p.category)
            t, m = await render_money(s, new)
            await cb.message.edit_text(t, reply_markup=m)
    await cb.answer()
