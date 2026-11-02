from datetime import date, datetime, time
from decimal import Decimal

import pytest

from bot.services.parser import classify, parse_money, parse_task

NOW = datetime(2026, 11, 2, 10, 0)  # понедельник
TODAY = NOW.date()


@pytest.mark.parametrize("text,amount,currency,kind", [
    ("кофе 25к", 25_000, "UZS", "expense"),
    ("такси 30 000", 30_000, "UZS", "expense"),
    ("такси 30000", 30_000, "UZS", "expense"),
    ("+5 млн зарплата", 5_000_000, "UZS", "income"),
    ("зарплата 8 млн", 8_000_000, "UZS", "income"),
    ("2.5 млн аренда", 2_500_000, "UZS", "expense"),
    ("$100 подписка", 100, "USD", "expense"),
    ("подписка 20$", 20, "USD", "expense"),
    ("обед 85 000 сум", 85_000, "UZS", "expense"),
    ("2 кофе 25к", 25_000, "UZS", "expense"),
    ("самса 12.000", 12_000, "UZS", "expense"),
    ("бензин 150k", 150_000, "UZS", "expense"),
])
def test_money_amounts(text, amount, currency, kind):
    m = parse_money(text, TODAY)
    assert m is not None
    assert m.amount == Decimal(amount)
    assert m.currency == currency
    assert m.kind == kind


def test_money_category_and_note():
    m = parse_money("Кофе 25к", TODAY)
    assert m.category == "Кафе"
    assert m.note == "Кофе"
    assert parse_money("такси 30000", TODAY).category == "Транспорт"
    assert parse_money("самса 12к", TODAY, learned={"самса": "Продукты"}).category == "Продукты"


def test_money_yesterday():
    m = parse_money("вчера такси 30к", TODAY)
    assert m.day == date(2026, 11, 1)
    assert m.note == "такси"


def test_task_dates():
    t = parse_task("завтра в 15:00 созвон с Азизом", NOW)
    assert t.due_date == date(2026, 11, 3)
    assert t.due_time == time(15, 0)
    assert t.title == "Созвон с Азизом"

    t = parse_task("в пятницу отправить отчёт", NOW)
    assert t.due_date == date(2026, 11, 6)
    assert t.title == "Отправить отчёт"

    t = parse_task("позвонить маме в 7 вечера", NOW)
    assert t.due_time == time(19, 0)
    assert t.due_date == TODAY

    t = parse_task("напомни в 9:30 выпить витамины", NOW)
    assert t.due_date == date(2026, 11, 3)  # 9:30 уже прошло
    assert t.title == "Выпить витамины"

    t = parse_task("срочно оплатить интернет 15.11", NOW)
    assert t.priority == "A"
    assert t.due_date == date(2026, 11, 15)


@pytest.mark.parametrize("text,kind", [
    ("кофе 25к", "money"),
    ("+5 млн зарплата", "money"),
    ("завтра в 15:00 созвон", "task"),
    ("купить молоко", "task"),
    ("задача сделать презентацию", "task"),
    ("идея: бот для учёта клиентов салона", "idea"),
    ("приложение которое напоминает пить воду", "idea"),
    ("завтра оплатить интернет 120к", "task"),
])
def test_classify(text, kind):
    assert classify(text, NOW).type == kind
