"""Финансы: карточка подтверждения, счета, отчёты, курс, отмена."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Account, Category, Draft, Transaction, UserSettings
from bot.db.session import Session
from bot.services import repo
from bot.services.currency import get_rate
from bot.services.parser import key_word
from bot.ui import bar, human_date, kb, money

router = Router(name="money")


# --- черновик -----------------------------------------------------------------

async def new_money_draft(s: AsyncSession, *, text: str, kind: str, amount: Decimal, currency: str,
                          note: str, day: date, category_name: str | None) -> Draft:
    acc = await repo.default_account(s, currency)
    cat = await repo.category_by_name(s, category_name, kind)
    draft = Draft(kind="money", created_at=repo.now_local(), data={
        "text": text, "kind": kind, "amount": str(amount), "currency": currency,
        "account_id": acc.id, "category_id": cat.id if cat else None, "note": note,
        "day": day.isoformat(), "picked": False,
    })
    s.add(draft)
    await s.commit()
    return draft


async def render_money(s: AsyncSession, draft: Draft) -> tuple[str, object]:
    d = draft.data
    today = repo.now_local().date()
    acc = await s.get(Account, d["account_id"])
    cat = await s.get(Category, d["category_id"]) if d.get("category_id") else None
    head = "💸 <b>Расход</b>" if d["kind"] == "expense" else "💰 <b>Доход</b>"
    lines = [
        f"{head} · <b>{money(Decimal(d['amount']), d['currency'])}</b>",
        f"🏷 {cat.label if cat else '<i>выберите категорию ↓</i>'}",
        f"💳 {escape(acc.name) if acc else '—'}",
    ]
    if d.get("note"):
        lines.append(f"📝 {escape(d['note'])}")
    day = date.fromisoformat(d["day"])
    if day != today:
        lines.append(f"📅 {human_date(day, today)}")
    i = draft.id
    if cat is None:
        return "\n".join(lines), await category_kb(s, draft)
    rows = [
        [("✅ Сохранить", f"m:ok:{i}"), ("✖️", f"m:no:{i}")],
        [("🏷 Категория", f"m:cat:{i}"), ("💳 Счёт", f"m:acc:{i}"),
         ("⇄ Доход" if d["kind"] == "expense" else "⇄ Расход", f"m:kind:{i}")],
        [("📋 Это задача", f"d:task:{i}"), ("💡 Это идея", f"d:idea:{i}")],
    ]
    return "\n".join(lines), kb(rows)


async def category_kb(s: AsyncSession, draft: Draft):
    cats = await repo.categories(s, draft.data["kind"])
    buttons = [(c.label, f"m:setc:{draft.id}:{c.id}") for c in cats]
    rows = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
    rows.append([("⇄ Это доход" if draft.data["kind"] == "expense" else "⇄ Это расход", f"m:kind:{draft.id}"),
                 ("✖️ Отмена", f"m:no:{draft.id}")])
    return kb(rows)


async def _load(s: AsyncSession, cb: CallbackQuery, draft_id: str) -> Draft | None:
    draft = await s.get(Draft, int(draft_id))
    if draft is None:
        await cb.answer("Черновик устарел", show_alert=True)
        await cb.message.edit_reply_markup(reply_markup=None)
    return draft


async def _refresh(s: AsyncSession, cb: CallbackQuery, draft: Draft) -> None:
    text, markup = await render_money(s, draft)
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


def _set(draft: Draft, **changes) -> None:
    draft.data = {**draft.data, **changes}  # новый dict, чтобы SQLAlchemy увидел изменение JSON


@router.callback_query(F.data.startswith("m:"))
async def money_cb(cb: CallbackQuery) -> None:
    _, action, draft_id, *rest = cb.data.split(":")
    async with Session() as s:
        if action == "del":  # отмена уже сохранённой записи
            tx = await s.get(Transaction, int(draft_id))
            if tx:
                await s.delete(tx)
                await s.commit()
            await cb.message.edit_text("↩️ Запись удалена")
            await cb.answer()
            return
        draft = await _load(s, cb, draft_id)
        if draft is None:
            return
        d = draft.data
        if action == "no":
            await s.delete(draft)
            await s.commit()
            await cb.message.edit_text("✖️ Отменено")
            await cb.answer()
        elif action == "cat":
            await cb.message.edit_reply_markup(reply_markup=await category_kb(s, draft))
            await cb.answer()
        elif action == "setc":
            _set(draft, category_id=int(rest[0]), picked=True)
            await s.commit()
            await _refresh(s, cb, draft)
        elif action == "kind":
            _set(draft, kind="income" if d["kind"] == "expense" else "expense", category_id=None)
            await s.commit()
            await _refresh(s, cb, draft)
        elif action == "acc":
            accs = await repo.accounts(s)
            ids = [a.id for a in accs]
            nxt = accs[(ids.index(d["account_id"]) + 1) % len(accs)] if d["account_id"] in ids else accs[0]
            _set(draft, account_id=nxt.id, currency=nxt.currency)
            await s.commit()
            await _refresh(s, cb, draft)
        elif action == "ok":
            await save_money(s, cb, draft)


async def save_money(s: AsyncSession, cb: CallbackQuery, draft: Draft) -> None:
    d = draft.data
    if not d.get("category_id"):
        await cb.answer("Сначала выберите категорию")
        return
    tx = Transaction(kind=d["kind"], amount=Decimal(d["amount"]), currency=d["currency"],
                     account_id=d["account_id"], category_id=d["category_id"], note=d.get("note") or "",
                     day=date.fromisoformat(d["day"]), created_at=repo.now_local())
    s.add(tx)
    if d.get("picked") and (kw := key_word(d.get("note") or "")):
        await repo.learn(s, kw, d["category_id"])  # запоминаем: в следующий раз категория подставится сама
    await s.delete(draft)
    await s.commit()
    await s.refresh(tx, ["category", "account"])

    sign = "−" if tx.kind == "expense" else "+"
    text = f"✅ {tx.category.label if tx.category else ''} · <b>{sign}{money(tx.amount, tx.currency)}</b> · {escape(tx.account.name)}"
    if tx.note:
        text += f"\n📝 {escape(tx.note)}"
    text += await budget_line(s)
    await cb.message.edit_text(text, reply_markup=kb([[("↩️ Отменить", f"m:del:{tx.id}")]]))
    await cb.answer("Сохранено")


async def budget_line(s: AsyncSession) -> str:
    st = await s.get(UserSettings, 1)
    if not st or not st.monthly_budget:
        return ""
    today = repo.now_local().date()
    month_start = today.replace(day=1)
    spent = await repo.spent(s, month_start, today, today)
    left = Decimal(st.monthly_budget) - spent
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    days_left = (next_month - today).days
    if left <= 0:
        return f"\n\n🚨 Бюджет месяца превышен на {money(-left)}"
    return f"\n\n📊 До конца месяца осталось {money(left)} — по {money(left / days_left)} в день"


# --- команды ------------------------------------------------------------------

@router.message(Command("undo"))
async def undo(message: Message) -> None:
    async with Session() as s:
        tx = await s.scalar(select(Transaction).order_by(Transaction.id.desc()).limit(1))
        if not tx:
            await message.answer("Нечего отменять")
            return
        label = f"{tx.category.label if tx.category else ''} {money(tx.amount, tx.currency)} {escape(tx.note)}"
        await s.delete(tx)
        await s.commit()
    await message.answer(f"↩️ Удалил последнюю запись: {label}")


@router.message(Command("balance", "accounts"))
async def balance(message: Message) -> None:
    async with Session() as s:
        rows = await repo.balances(s)
        today = repo.now_local().date()
        rate = await get_rate(s, "USD", today)
    total = sum((b * (rate if a.currency == "USD" else 1) for a, b in rows), Decimal(0))
    lines = ["💳 <b>Счета</b>"] + [f"• {escape(a.name)}: <b>{money(b, a.currency)}</b>" for a, b in rows]
    lines += ["", f"Всего ≈ <b>{money(total)}</b>", f"<i>Курс ЦБ: $1 = {money(rate)}</i>",
              "", "Начальный остаток: <code>/setbalance Наличные 500к</code>"]
    await message.answer("\n".join(lines))


@router.message(Command("setbalance"))
async def set_balance(message: Message) -> None:
    from bot.services.parser import parse_money

    args = (message.text or "").split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Формат: <code>/setbalance Наличные 500к</code>\nУкажите, сколько денег на счёте сейчас.")
        return
    name, amount_text = args[1], args[2]
    parsed = parse_money(amount_text, repo.now_local().date())
    if not parsed:
        await message.answer("Не понял сумму")
        return
    async with Session() as s:
        acc = next((a for a in await repo.accounts(s) if a.name.casefold() == name.casefold()), None)
        if not acc:
            names = ", ".join(a.name for a in await repo.accounts(s))
            await message.answer(f"Нет счёта «{escape(name)}». Есть: {escape(names)}")
            return
        current = dict((a.id, b) for a, b in await repo.balances(s))[acc.id]
        acc.initial_balance = Decimal(acc.initial_balance) + parsed.amount - current
        await s.commit()
    await message.answer(f"✅ Остаток «{escape(acc.name)}» теперь {money(parsed.amount, acc.currency)}")


@router.message(Command("rate"))
async def rate_cmd(message: Message) -> None:
    async with Session() as s:
        rate = await get_rate(s, "USD", repo.now_local().date())
    await message.answer(f"💵 Курс ЦБ: $1 = <b>{money(rate)}</b>")


def period_bounds(code: str, today: date) -> tuple[date, date, str]:
    if code == "day":
        return today, today, "сегодня"
    if code == "yday":
        y = today - timedelta(days=1)
        return y, y, "вчера"
    if code == "week":
        return today - timedelta(days=today.weekday()), today, "эту неделю"
    if code == "prev":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, "прошлый месяц"
    return today.replace(day=1), today, "этот месяц"


async def report_text(s: AsyncSession, code: str) -> str:
    today = repo.now_local().date()
    start, end, title = period_bounds(code, today)
    r = await repo.report(s, start, end, today)
    if r.count == 0:
        return f"📊 За {title} записей нет."
    lines = [f"📊 <b>Отчёт за {title}</b>", "",
             f"💸 Расходы: <b>{money(r.expense)}</b>", f"💰 Доходы: <b>{money(r.income)}</b>",
             f"{'📈' if r.income >= r.expense else '📉'} Итого: <b>{money(r.income - r.expense)}</b>"]
    if r.by_category:
        lines += ["", "<b>По категориям:</b>"]
        for label, v in r.by_category:
            pct = round(float(v / r.expense) * 100) if r.expense else 0
            lines.append(f"{escape(label)} — {money(v)} ({pct}%)\n<code>{bar(v, r.expense)}</code>")
    return "\n".join(lines)


REPORT_KB = kb([[("Сегодня", "r:day"), ("Вчера", "r:yday"), ("Неделя", "r:week")],
                [("Месяц", "r:month"), ("Прошлый месяц", "r:prev")]])


@router.message(Command("report"))
async def report_cmd(message: Message) -> None:
    async with Session() as s:
        await message.answer(await report_text(s, "month"), reply_markup=REPORT_KB)


@router.callback_query(F.data.startswith("r:"))
async def report_cb(cb: CallbackQuery) -> None:
    async with Session() as s:
        text = await report_text(s, cb.data[2:])
    if text != cb.message.html_text:
        await cb.message.edit_text(text, reply_markup=REPORT_KB)
    await cb.answer()
