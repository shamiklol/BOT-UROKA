"""Разбор фраз без ИИ: суммы, даты, категории, тип сообщения.

Чистые функции без обращения к базе — их легко тестировать.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

# --- суммы -------------------------------------------------------------------

_NUM = r"\d{1,3}(?:[  .,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?"
_MULT = r"млн|миллион\w*|mln|тыс\w*|k|к|m|т"
_CUR = r"\$|usd|долл\w*|сум\w*|sum|uzs|so'?m"
_MONEY_RE = re.compile(
    rf"(?P<sign>[+\-−])?\s*(?P<pre>\$)?\s*(?P<num>{_NUM})\s*"
    rf"(?P<mult>{_MULT})?(?![a-zа-яё\d])\s*(?P<cur>{_CUR})?(?![a-zа-яё])",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\b(?:в\s*)?([01]?\d|2[0-3])[:.]([0-5]\d)\b")

INCOME_WORDS = ("зарплат", "зп", "аванс", "оклад", "доход", "получил", "пришл", "кешбэк", "кэшбэк",
                "вернули", "бонус", "премия", "премию", "дивиденд")


def _to_decimal(num: str, has_mult: bool) -> Decimal | None:
    raw = num.replace(" ", " ").strip()
    try:
        if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", raw) and not has_mult:
            return Decimal(re.sub(r"[ .,]", "", raw))
        if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+[.,]\d{1,2}", raw):
            head, tail = raw[:-3], raw[-3:]
            if tail[0] in ".,":
                return Decimal(re.sub(r"[ .,]", "", head) + "." + tail[1:])
        return Decimal(raw.replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None


def _mult_value(mult: str | None) -> int:
    if not mult:
        return 1
    m = mult.lower()
    if m.startswith(("млн", "миллион", "mln")) or m == "m":
        return 1_000_000
    return 1_000


@dataclass
class MoneyParse:
    kind: str  # expense | income
    amount: Decimal
    currency: str  # UZS | USD
    note: str
    day: date
    category: str | None = None
    strong: bool = True  # уверены, что это деньги


def parse_money(text: str, today: date, learned: dict[str, str] | None = None) -> MoneyParse | None:
    low = text.lower().replace("ё", "е")
    # Время и даты вида 15:00 / 12.05 не считаем суммами.
    masked = _TIME_RE.sub(lambda m: " " * len(m.group(0)), low)
    masked = re.sub(rf"\b\d{{1,2}}\.\d{{1,2}}(?:\.\d{{2,4}})?\b(?!\s*(?:{_MULT}|{_CUR}))",
                    lambda m: " " * len(m.group(0)), masked, flags=re.IGNORECASE)

    best = None
    for m in _MONEY_RE.finditer(masked):
        amount = _to_decimal(m.group("num"), bool(m.group("mult")))
        if amount is None or amount <= 0:
            continue
        amount *= _mult_value(m.group("mult"))
        signal = bool(m.group("mult") or m.group("cur") or m.group("pre") or m.group("sign"))
        score = (signal, amount)
        if best is None or score > best[0]:
            best = (score, m, amount)
    if best is None:
        return None
    (signal, _), m, amount = best

    cur_raw = (m.group("pre") or m.group("cur") or "").lower()
    currency = "USD" if cur_raw in ("$", "usd") or cur_raw.startswith("долл") else "UZS"
    sign = m.group("sign")

    note = (low[: m.start()] + " " + low[m.end():])
    day = today
    if re.search(r"\bпозавчера\b", note):
        day = today - timedelta(days=2)
    elif re.search(r"\bвчера\b", note):
        day = today - timedelta(days=1)
    note = re.sub(r"\b(позавчера|вчера|сегодня|потратил[аи]?|заплатил[аи]?|за|на)\b", " ", note)
    note = re.sub(r"\s+", " ", note).strip(" -—:,.")

    kind = "income" if sign == "+" or any(w in low for w in INCOME_WORDS) else "expense"
    strong = signal or amount >= 1000
    category = guess_category(note, kind, learned)
    original_note = _restore_case(text, note)
    return MoneyParse(kind=kind, amount=amount, currency=currency, note=original_note,
                      day=day, category=category, strong=strong)


def _restore_case(original: str, note_lower: str) -> str:
    """Возвращает исходное написание заметки, если его можно найти."""
    if not note_lower:
        return ""
    idx = original.lower().replace("ё", "е").find(note_lower)
    return original[idx: idx + len(note_lower)] if idx >= 0 else note_lower


# --- категории ---------------------------------------------------------------

EXPENSE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Продукты": ("продукт", "магазин", "корзинк", "korzinka", "макро", "makro", "хавас", "havas", "хлеб",
                 "молок", "мяс", "овощ", "фрукт", "базар", "бозор", "супермаркет", "яйц", "вод"),
    "Кафе": ("кофе", "кафе", "ресторан", "обед", "ужин", "завтрак", "самс", "плов", "шаурм", "бургер",
             "пицц", "лаваш", "фастфуд", "эвос", "evos", "kfc", "чайхан", "суши", "донер"),
    "Транспорт": ("такси", "яндекс", "yandex", "mytaxi", "метро", "автобус", "бензин", "заправк", "метан",
                  "пропан", "парковк", "мойк", "uber", "проезд"),
    "Дом": ("аренд", "квартир", "коммунал", "свет", "электр", "газ", "ремонт", "мебел", "хозтовар"),
    "Связь": ("телефон", "мобил", "интернет", "beeline", "билайн", "ucell", "юсел", "uzmobile", "mobiuz",
              "связь", "uztelecom"),
    "Здоровье": ("аптек", "лекарств", "врач", "клиник", "анализ", "стоматолог", "фитнес", "спортзал", "зал"),
    "Одежда": ("одежд", "обув", "кроссов", "футболк", "куртк", "джинс", "рубашк", "платье"),
    "Развлечения": ("кино", "игр", "концерт", "боулинг", "развлеч", "кальян", "бильярд"),
    "Подписки": ("подписк", "netflix", "spotify", "youtube", "chatgpt", "claude", "icloud", "apple music",
                 "telegram premium"),
    "Обучение": ("курс", "книг", "обучен", "урок", "учеб", "репетитор"),
    "Подарки": ("подар", "цвет"),
    "Бизнес": ("реклам", "таргет", "хостинг", "домен", "railway", "сервер", "сотрудник"),
}
INCOME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Зарплата": ("зарплат", "зп", "аванс", "оклад", "премия", "премию", "бонус"),
    "Бизнес": ("клиент", "заказ", "продаж", "оплата", "проект"),
    "Подарок": ("подар",),
}


def words_of(text: str) -> list[str]:
    return re.findall(r"[a-zа-яёўқғҳ']+", text.lower().replace("ё", "е"))


def guess_category(note: str, kind: str, learned: dict[str, str] | None = None) -> str | None:
    words = words_of(note)
    if learned:
        for w in words:
            if w in learned:
                return learned[w]
    table = INCOME_KEYWORDS if kind == "income" else EXPENSE_KEYWORDS
    for w in words:
        for cat, stems in table.items():
            if any(w.startswith(s) for s in stems):
                return cat
    return None


def key_word(note: str) -> str | None:
    """Слово, которое бот запомнит для категории (первое осмысленное)."""
    for w in words_of(note):
        if len(w) >= 3:
            return w
    return None


# --- задачи и даты -----------------------------------------------------------

WEEKDAYS = {
    "понедельник": 0, "пн": 0, "вторник": 1, "вт": 1, "среду": 2, "среда": 2, "ср": 2,
    "четверг": 3, "чт": 3, "пятницу": 4, "пятница": 4, "пт": 4, "субботу": 5, "суббота": 5, "сб": 5,
    "воскресенье": 6, "вс": 6,
}
TASK_VERBS = ("напомни", "надо", "нужно", "сделать", "сделай", "позвонить", "позвони", "написать",
              "напиши", "купить", "отправить", "встреча", "созвон", "задача", "оплатить", "забрать",
              "записаться", "подготовить", "проверить", "сходить", "съездить")
_FILLER = re.compile(r"^(задача|напомни(?:ть)?(?: мне)?|надо|нужно)\b[:\s,-]*", re.IGNORECASE)


@dataclass
class TaskParse:
    title: str
    due_date: date | None = None
    due_time: time | None = None
    priority: str = "B"
    has_marker: bool = False  # есть явные признаки задачи
    tags: list[str] = field(default_factory=list)


def parse_task(text: str, now: datetime) -> TaskParse:
    today = now.date()
    s = " " + text.strip() + " "
    low = s.lower().replace("ё", "е")
    due_date: date | None = None
    due_time: time | None = None
    marker = False

    def cut(m: re.Match) -> None:
        nonlocal s, low
        s = s[: m.start()] + " " + s[m.end():]
        low = low[: m.start()] + " " + low[m.end():]

    for pattern, delta in ((r"\bпослезавтра\b", 2), (r"\bзавтра\b", 1), (r"\bсегодня\b", 0)):
        m = re.search(pattern, low)
        if m:
            due_date = today + timedelta(days=delta)
            cut(m)
            marker = True
            break

    if due_date is None:
        m = re.search(r"\bчерез\s+(\d+)\s+(дн\w*|недел\w*)", low)
        if m:
            n = int(m.group(1))
            due_date = today + timedelta(days=n * (7 if m.group(2).startswith("недел") else 1))
            cut(m)
            marker = True
    if due_date is None:
        m = re.search(r"\b(?:в|во|на)?\s*(" + "|".join(WEEKDAYS) + r")\b", low)
        if m:
            wd = WEEKDAYS[m.group(1)]
            ahead = (wd - today.weekday()) % 7 or 7
            due_date = today + timedelta(days=ahead)
            cut(m)
            marker = True
    if due_date is None:
        m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b", low)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else today.year
            if y < 100:
                y += 2000
            try:
                due_date = date(y, mo, d)
                if not m.group(3) and due_date < today:
                    due_date = date(y + 1, mo, d)
                cut(m)
                marker = True
            except ValueError:
                pass

    m = re.search(r"\b(?:в\s*)?([01]?\d|2[0-3])[:.]([0-5]\d)\b", low)
    if m:
        due_time = time(int(m.group(1)), int(m.group(2)))
        cut(m)
        marker = True
    else:
        m = re.search(r"\bв\s+(\d{1,2})\s*(утра|дня|вечера|ночи|час\w*)?(?![\d.,:])\b", low)
        if m and int(m.group(1)) <= 23:
            h = int(m.group(1))
            if m.group(2) in ("вечера", "дня") and h < 12:
                h += 12
            due_time = time(h, 0)
            cut(m)
            marker = True

    if due_time and due_date is None:
        due_date = today if due_time > now.time() else today + timedelta(days=1)

    priority = "B"
    if re.search(r"(!!|\bсрочно\b|\bважно\b)", low):
        priority = "A"
        s = re.sub(r"(!!|\bсрочно\b|\bважно\b)", " ", s, flags=re.IGNORECASE)

    if any(low.strip().startswith(v) for v in TASK_VERBS):
        marker = True

    title = re.sub(r"\s+", " ", s).strip(" ,.-—:")
    title = _FILLER.sub("", title).strip(" ,.-—:")
    title = title[:1].upper() + title[1:] if title else text.strip()
    return TaskParse(title=title, due_date=due_date, due_time=due_time, priority=priority,
                     has_marker=marker, tags=re.findall(r"#(\w+)", text))


# --- общий классификатор -----------------------------------------------------

@dataclass
class Classified:
    type: str  # money | task | idea
    confident: bool
    money: MoneyParse | None = None
    task: TaskParse | None = None
    idea_text: str = ""
    tags: list[str] = field(default_factory=list)


def classify(text: str, now: datetime, learned: dict[str, str] | None = None) -> Classified:
    stripped = text.strip()
    low = stripped.lower()
    tags = re.findall(r"#(\w+)", stripped)

    if re.match(r"^(идея|💡|мысль|заметка)\b", low) or low.startswith("💡"):
        body = re.sub(r"^(идея|💡|мысль|заметка)[:\s,-]*", "", stripped, flags=re.IGNORECASE)
        return Classified("idea", True, idea_text=body or stripped, tags=tags)

    task = parse_task(stripped, now)
    money = parse_money(stripped, now.date(), learned)
    explicit_task = bool(re.match(r"^(задача|напомни)", low))

    # Дата/время/глагол-действие → задача («завтра оплатить интернет 120к»),
    # иначе сумма → трата («оплатил интернет 120к»).
    if explicit_task or task.has_marker:
        return Classified("task", True, task=task, tags=tags)
    if money and money.strong:
        return Classified("money", True, money=money, tags=tags)
    return Classified("idea", False, idea_text=stripped, tags=tags)
