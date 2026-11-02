"""Сквозная проверка: сообщения и кнопки идут через настоящий Dispatcher,
а запросы к Telegram перехватывает поддельная сессия."""
import os
import tempfile
from datetime import datetime

DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(BOT_TOKEN="123456:TEST-token-abcdefghijklmnopqrstuvwxyz", OWNER_ID="1",
                  DATABASE_URL=f"sqlite+aiosqlite:///{DB}", ANTHROPIC_API_KEY="", OPENAI_API_KEY="")

import pytest  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, SendMessage  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Message, Update, User  # noqa: E402

from bot.handlers import capture, common, habits, ideas, money, tasks  # noqa: E402
from bot.middlewares import OwnerOnly  # noqa: E402


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list = []
        self.msg_id = 100

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            self.msg_id += 1
            return Message(message_id=self.msg_id, date=datetime.now(), chat=Chat(id=1, type="private"),
                           text=method.text, reply_markup=method.reply_markup)
        if isinstance(method, EditMessageReplyMarkup):
            return True
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""


USER = User(id=1, is_bot=False, first_name="Тест")
STRANGER = User(id=2, is_bot=False, first_name="Чужой")
_uid = [0]

DP = Dispatcher()
DP.message.outer_middleware(OwnerOnly())
DP.callback_query.outer_middleware(OwnerOnly())
DP.include_routers(common.router, habits.router, money.router, tasks.router, ideas.router, capture.router)


@pytest.fixture
async def env():
    from bot.db.session import init_db

    await init_db()
    session = FakeSession()
    bot = Bot("123456:TEST-token-abcdefghijklmnopqrstuvwxyz", session=session)
    dp = DP

    async def say(text, user=USER):
        _uid[0] += 1
        session.sent.clear()
        msg = Message(message_id=_uid[0], date=datetime.now(), chat=Chat(id=user.id, type="private"),
                      from_user=user, text=text)
        await dp.feed_update(bot, Update(update_id=_uid[0], message=msg))
        return [m for m in session.sent if isinstance(m, (SendMessage, EditMessageText))]

    async def press(data, text="card", user=USER):
        _uid[0] += 1
        session.sent.clear()
        msg = Message(message_id=50, date=datetime.now(), chat=Chat(id=1, type="private"), text=text)
        cb = CallbackQuery(id=str(_uid[0]), from_user=user, chat_instance="x", data=data, message=msg)
        await dp.feed_update(bot, Update(update_id=_uid[0], callback_query=cb))
        return session.sent

    yield say, press
    await bot.session.close()


def buttons(method):
    return [b.callback_data for row in method.reply_markup.inline_keyboard for b in row]


async def test_expense_flow(env):
    say, press = env
    out = await say("кофе 25к")
    card = out[-1]
    assert "25 000 сум" in card.text and "Кафе" in card.text
    ok = next(b for b in buttons(card) if b.startswith("m:ok:"))
    sent = await press(ok)
    saved = next(m for m in sent if isinstance(m, EditMessageText))
    assert "−25 000 сум" in saved.text

    report = (await say("/report"))[-1]
    assert "25 000 сум" in report.text and "Кафе" in report.text

    undo = (await say("/undo"))[-1]
    assert "Удалил" in undo.text


async def test_unknown_category_is_learned(env):
    say, press = env
    card = (await say("чучвара 30к"))[-1]
    assert "выберите категорию" in card.text
    cat = next(b for b in buttons(card) if b.startswith("m:setc:"))  # первая категория — Продукты
    card2 = next(m for m in await press(cat) if isinstance(m, EditMessageText))
    await press(next(b for b in buttons(card2) if b.startswith("m:ok:")))
    card3 = (await say("чучвара 40к"))[-1]
    assert "Продукты" in card3.text  # бот запомнил слово


async def test_task_flow_and_plan(env):
    say, press = env
    card = (await say("сегодня срочно сдать отчёт"))[-1]
    assert "Задача" in card.text and "🔴" in card.text
    sent = await press(next(b for b in buttons(card) if b.startswith("td:ok:")))
    assert any("Задача добавлена" in m.text for m in sent if isinstance(m, EditMessageText))

    plan = (await say("/plan"))[-1]
    top_btn = next(b for b in buttons(plan) if b.startswith("tk:top:"))
    sent = await press(top_btn)
    assert any("Выбрано: 1/3" in m.text for m in sent if isinstance(m, EditMessageText))

    today = (await say("/today"))[-1]
    assert "Главное" in today.text and "Сдать отчёт" in today.text
    done_btn = next(b for b in buttons(today) if b.startswith("tk:done:"))
    sent = await press(done_btn)
    assert any(isinstance(m, AnswerCallbackQuery) and m.text == "Готово! 🎉" for m in sent)


async def test_idea_and_convert(env):
    say, press = env
    out = (await say("приложение для учёта клиентов барбершопа #бизнес"))[-1]
    assert "входящие" in out.text
    ideas_list = (await say("/ideas"))[-1]
    assert "барбершопа" in ideas_list.text
    task_btn = next(b for b in buttons(out) if b.startswith("i:task:"))
    sent = await press(task_btn)
    assert any("Перенёс в задачи" in m.text for m in sent if isinstance(m, EditMessageText))


async def test_money_to_task_conversion(env):
    say, press = env
    card = (await say("интернет 120к"))[-1]
    conv = next(b for b in buttons(card) if b.startswith("d:task:"))
    sent = await press(conv)
    assert any("Задача" in m.text for m in sent if isinstance(m, EditMessageText))


async def test_habits(env):
    say, press = env
    out = (await say("/habit Зарядка 07:30"))[-1]
    assert "Зарядка" in out.text and "07:30" in out.text
    view = (await say("/habits"))[-1]
    toggle = next(b for b in buttons(view) if b.startswith("hb:t:"))
    sent = await press(toggle)
    assert any("1/1" in m.text for m in sent if isinstance(m, EditMessageText))


async def test_summaries_and_settings(env):
    say, _ = env
    assert "Доброе утро" in (await say("/summary"))[-1].text
    assert "Итог дня" in (await say("/review"))[-1].text
    assert "6 000 000 сум" in (await say("/budget 6 млн"))[-1].text
    assert "07:00" in (await say("/morning 7:00"))[-1].text
    assert "Бюджет на месяц" in (await say("/settings"))[-1].text


async def test_stranger_is_ignored(env):
    say, press = env
    assert await say("кофе 25к", user=STRANGER) == []


async def test_scheduler_tick(env):
    from bot import scheduler

    session = FakeSession()
    bot = Bot("123456:TEST-token-abcdefghijklmnopqrstuvwxyz", session=session)
    await scheduler.tick(bot)  # не должно падать; сводки зависят от текущего времени
    await scheduler.tick(bot)
    mornings = [m for m in session.sent if isinstance(m, SendMessage) and "Доброе утро" in m.text]
    assert len(mornings) <= 1  # без дублей
