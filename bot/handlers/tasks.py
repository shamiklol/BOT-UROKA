"""Задачи и планёрка: черновик задачи, списки, «Топ-3 дня», карточка задачи."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Draft, Task
from bot.db.session import Session
from bot.services import repo
from bot.services.summaries import evening_kb
from bot.ui import PRIORITY, hm, human_date, kb, short, task_line

router = Router(name="tasks")
TOP_LIMIT = 3


# --- черновик задачи ------------------------------------------------------------

async def new_task_draft(s: AsyncSession, *, text: str, title: str, due_date: date | None,
                         due_time: time | None, priority: str) -> Draft:
    draft = Draft(kind="task", created_at=repo.now_local(), data={
        "text": text, "title": title, "due_date": due_date.isoformat() if due_date else None,
        "due_time": due_time.strftime("%H:%M") if due_time else None, "priority": priority,
    })
    s.add(draft)
    await s.commit()
    return draft


def render_task_draft(draft: Draft):
    d = draft.data
    today = repo.now_local().date()
    due = date.fromisoformat(d["due_date"]) if d["due_date"] else None
    when = human_date(due, today) + (f", {d['due_time']}" if d["due_time"] else "")
    text = (f"📋 <b>Задача</b>\n{escape(d['title'])}\n\n"
            f"📅 {when} · {PRIORITY[d['priority']]} приоритет {d['priority']}")
    if d["due_time"]:
        text += "\n⏰ Напомню в это время"
    i = draft.id
    p = d["priority"]
    rows = [
        [("✅ Сохранить", f"td:ok:{i}"), ("✖️", f"td:no:{i}")],
        [("Сегодня", f"td:d:{i}:0"), ("Завтра", f"td:d:{i}:1"), ("Без даты", f"td:d:{i}:-")],
        [((("• " if p == x else "") + PRIORITY[x] + " " + x), f"td:p:{i}:{x}") for x in "ABC"],
        [("💰 Это трата", f"d:money:{i}"), ("💡 Это идея", f"d:idea:{i}")],
    ]
    return text, kb(rows)


@router.callback_query(F.data.startswith("td:"))
async def task_draft_cb(cb: CallbackQuery) -> None:
    _, action, draft_id, *rest = cb.data.split(":")
    async with Session() as s:
        draft = await s.get(Draft, int(draft_id))
        if draft is None:
            await cb.answer("Черновик устарел", show_alert=True)
            await cb.message.edit_reply_markup(reply_markup=None)
            return
        d = dict(draft.data)
        today = repo.now_local().date()
        if action == "no":
            await s.delete(draft)
            await s.commit()
            await cb.message.edit_text("✖️ Отменено")
            return await cb.answer()
        if action == "ok":
            task = Task(
                title=d["title"], priority=d["priority"], created_at=repo.now_local(),
                due_date=date.fromisoformat(d["due_date"]) if d["due_date"] else None,
                due_time=time.fromisoformat(d["due_time"]) if d["due_time"] else None,
            )
            s.add(task)
            await s.delete(draft)
            await s.commit()
            await cb.message.edit_text(f"✅ Задача добавлена\n{task_line(task, today)}",
                                       reply_markup=kb([[("📋 Сегодня", "tk:today"), ("🗂 Все задачи", "tk:all")]]))
            return await cb.answer("Сохранено")
        if action == "d":
            d["due_date"] = None if rest[0] == "-" else (today + timedelta(days=int(rest[0]))).isoformat()
            if d["due_date"] is None:
                d["due_time"] = None
        elif action == "p":
            d["priority"] = rest[0]
        draft.data = d
        await s.commit()
        text, markup = render_task_draft(draft)
        await cb.message.edit_text(text, reply_markup=markup)
        await cb.answer()


# --- списки --------------------------------------------------------------------

async def today_view(s: AsyncSession) -> tuple[str, object]:
    today = repo.now_local().date()
    tasks = await repo.today_tasks(s, today)
    done = await repo.done_today(s, today)
    if not tasks:
        text = "📋 <b>На сегодня задач нет</b>"
        if done:
            text += f"\n\n✅ Уже сделано: {len(done)}"
        text += "\n\nНапишите задачу, например: <i>завтра в 15:00 созвон</i>"
        return text, kb([[("🗂 Все задачи", "tk:all")]])
    top = [t for t in tasks if t.is_top]
    rest = [t for t in tasks if not t.is_top]
    lines = [f"📋 <b>Сегодня</b> · {len(tasks)} в работе" + (f" · ✅ {len(done)}" if done else "")]
    if top:
        lines += ["", "<b>⭐ Главное:</b>"] + [task_line(t, today) for t in top]
    if rest:
        lines += ["", "<b>Остальное:</b>"] + [task_line(t, today) for t in rest]
    lines += ["", "<i>Нажмите задачу, чтобы отметить выполненной</i>"]
    rows = [[(f"✅ {short(t.title)}", f"tk:done:{t.id}:today")] for t in tasks[:12]]
    rows.append([("⭐ Спланировать день", "tk:plan"), ("🗂 Все задачи", "tk:all")])
    return "\n".join(lines), kb(rows)


async def all_view(s: AsyncSession) -> tuple[str, object]:
    today = repo.now_local().date()
    sections = [
        ("📌 Сегодня и просрочено", await repo.today_tasks(s, today)),
        ("🗓 Скоро (2 недели)", await repo.upcoming_tasks(s, today)),
        ("💭 Когда-нибудь", await repo.someday_tasks(s)),
    ]
    lines = ["🗂 <b>Все задачи</b>"]
    for title, tasks in sections:
        if tasks:
            lines += ["", f"<b>{title}:</b>"] + [task_line(t, today) for t in tasks[:25]]
    if len(lines) == 1:
        lines.append("\nПусто. Напишите задачу, например: <i>в пятницу отправить отчёт</i>")
    lines += ["", "<i>Нажмите /t… рядом с задачей, чтобы изменить её</i>"]
    return "\n".join(lines), kb([[("📋 Сегодня", "tk:today"), ("⭐ Планёрка", "tk:plan")]])


async def plan_view(s: AsyncSession) -> tuple[str, object]:
    """Планёрка: выбрать до 3 главных задач дня."""
    today = repo.now_local().date()
    candidates = (await repo.today_tasks(s, today)) + (await repo.upcoming_tasks(s, today, days=3)) \
        + [t for t in await repo.someday_tasks(s) if t.priority == "A"]
    top_count = sum(1 for t in candidates if t.is_top)
    lines = ["⭐ <b>Планёрка</b>", "",
             f"Выберите до {TOP_LIMIT} главных задач на сегодня — то, что сделает день успешным.",
             f"Выбрано: {top_count}/{TOP_LIMIT}"]
    if not candidates:
        lines.append("\nЗадач пока нет. Напишите их сообщениями — по одной.")
    rows = [[(("⭐ " if t.is_top else "☆ ") + short(t.title, 30)
              + (f" · {human_date(t.due_date, today)}" if t.due_date and t.due_date != today else ""),
              f"tk:top:{t.id}")] for t in candidates[:15]]
    rows.append([("✅ Готово", "tk:today")])
    return "\n".join(lines), kb(rows)


def task_card(t: Task, today: date):
    status = "✅ выполнена" if t.done else "в работе"
    text = (f"📋 <b>{escape(t.title)}</b>\n\n📅 {human_date(t.due_date, today)}"
            + (f", {hm(t.due_time)}" if t.due_time else "")
            + f"\n{PRIORITY[t.priority]} приоритет {t.priority}" + (" · ⭐ главное" if t.is_top else "")
            + f"\nСтатус: {status}")
    i = t.id
    rows = [
        [("✅ Выполнено", f"tk:done:{i}:card")] if not t.done else [("↩️ Вернуть в работу", f"tk:undone:{i}")],
        [("Сегодня", f"tk:mv:{i}:0"), ("Завтра", f"tk:mv:{i}:1"), ("Через неделю", f"tk:mv:{i}:7"),
         ("Без даты", f"tk:mv:{i}:-")],
        [(PRIORITY[x] + " " + x, f"tk:pr:{i}:{x}") for x in "ABC"] + [("⭐", f"tk:topc:{i}")],
        [("🗑 Удалить", f"tk:del:{i}")],
    ]
    return text, kb(rows)


@router.message(Command("today"))
async def today_cmd(message: Message) -> None:
    async with Session() as s:
        text, markup = await today_view(s)
    await message.answer(text, reply_markup=markup)


@router.message(Command("tasks"))
async def tasks_cmd(message: Message) -> None:
    async with Session() as s:
        text, markup = await all_view(s)
    await message.answer(text, reply_markup=markup)


@router.message(Command("plan"))
async def plan_cmd(message: Message) -> None:
    async with Session() as s:
        text, markup = await plan_view(s)
    await message.answer(text, reply_markup=markup)


@router.message(F.text.regexp(r"^/t(\d+)(@\w+)?$").as_("m"))
async def task_open(message: Message, m) -> None:
    async with Session() as s:
        t = await s.get(Task, int(m.group(1)))
        if not t:
            await message.answer("Задача не найдена")
            return
        text, markup = task_card(t, repo.now_local().date())
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "ev:all")
async def move_all_tomorrow(cb: CallbackQuery) -> None:
    async with Session() as s:
        today = repo.now_local().date()
        left = [t for t in await repo.today_tasks(s, today) if t.due_date is None or t.due_date <= today]
        for t in left:
            t.due_date, t.reminded, t.is_top = today + timedelta(days=1), False, False
        await s.commit()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(f"➡️ Перенёс на завтра: {len(left)}. Хорошего вечера! 🌙")
    await cb.answer()


@router.callback_query(F.data.startswith("tk:"))
async def task_cb(cb: CallbackQuery) -> None:
    parts = cb.data.split(":")
    action = parts[1]
    async with Session() as s:
        now = repo.now_local()
        today = now.date()
        if action in ("today", "all", "plan"):
            view = {"today": today_view, "all": all_view, "plan": plan_view}[action]
            text, markup = await view(s)
            await cb.message.edit_text(text, reply_markup=markup)
            return await cb.answer()

        t = await s.get(Task, int(parts[2]))
        if t is None:
            return await cb.answer("Задача не найдена", show_alert=True)
        back = "card"
        note = None
        if action == "done":
            t.done, t.done_at, t.is_top = True, now, False
            back = parts[3] if len(parts) > 3 else "card"
            note = "Готово! 🎉"
        elif action == "undone":
            t.done, t.done_at = False, None
        elif action == "mv":
            t.due_date = None if parts[3] == "-" else today + timedelta(days=int(parts[3]))
            if t.due_date is None:
                t.due_time = None
            t.reminded, t.is_top = False, False
            back = parts[4] if len(parts) > 4 else "card"
            note = "Перенесено"
        elif action == "later":  # +1 час из напоминания
            base = datetime.combine(today, t.due_time or now.time())
            new = max(base, now) + timedelta(hours=1)
            t.due_date, t.due_time, t.reminded = new.date(), new.time().replace(second=0, microsecond=0), False
            await s.commit()
            await cb.message.edit_text(f"⏱ Напомню в {hm(t.due_time)}: {escape(t.title)}")
            return await cb.answer()
        elif action == "pr":
            t.priority = parts[3]
        elif action in ("top", "topc"):
            if not t.is_top:
                tops = [x for x in await repo.today_tasks(s, today) if x.is_top]
                if len(tops) >= TOP_LIMIT:
                    return await cb.answer(f"Уже выбрано {TOP_LIMIT} — снимите одну", show_alert=True)
                t.is_top = True
                if t.due_date is None or t.due_date > today:
                    t.due_date = today
            else:
                t.is_top = False
            back = "plan" if action == "top" else "card"
        elif action == "del":
            await s.delete(t)
            await s.commit()
            await cb.message.edit_text("🗑 Задача удалена")
            return await cb.answer()
        await s.commit()

        if back == "eve":  # вечерний итог: убираем строку с этой задачей, текст оставляем
            left = [x for x in await repo.today_tasks(s, today) if x.due_date is None or x.due_date <= today]
            await cb.message.edit_reply_markup(reply_markup=evening_kb(left))
            return await cb.answer(note)
        if back == "today":
            text, markup = await today_view(s)
        elif back == "plan":
            text, markup = await plan_view(s)
        elif back == "msg":  # из напоминания/вечернего итога — просто отметка в сообщении
            await cb.message.edit_reply_markup(reply_markup=None)
            await cb.message.answer(f"{'✅' if t.done else '➡️'} {escape(t.title)}"
                                    + (f" — {human_date(t.due_date, today)}" if not t.done else ""))
            return await cb.answer(note)
        else:
            text, markup = task_card(t, today)
        await cb.message.edit_text(text, reply_markup=markup)
        await cb.answer(note)
