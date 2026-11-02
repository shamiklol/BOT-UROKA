from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.models import Account, Base, Category, UserSettings

engine = create_async_engine(settings.db_url, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)

EXPENSE_CATEGORIES = [
    ("🍞", "Продукты"), ("☕", "Кафе"), ("🚕", "Транспорт"), ("🏠", "Дом"),
    ("📱", "Связь"), ("💊", "Здоровье"), ("👕", "Одежда"), ("🎉", "Развлечения"),
    ("🔁", "Подписки"), ("📚", "Обучение"), ("🎁", "Подарки"), ("💼", "Бизнес"),
    ("📦", "Другое"),
]
INCOME_CATEGORIES = [("💰", "Зарплата"), ("💼", "Бизнес"), ("🎁", "Подарок"), ("📥", "Другое")]


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as s:
        if await s.get(UserSettings, 1) is None:
            s.add(UserSettings(id=1))
        if not (await s.scalars(select(Account).limit(1))).first():
            s.add_all([
                Account(name="Наличные", currency="UZS", is_default=True),
                Account(name="Карта", currency="UZS"),
                Account(name="Доллары", currency="USD"),
            ])
        if not (await s.scalars(select(Category).limit(1))).first():
            s.add_all([Category(name=n, emoji=e, kind="expense") for e, n in EXPENSE_CATEGORIES])
            s.add_all([Category(name=n, emoji=e, kind="income") for e, n in INCOME_CATEGORIES])
        await s.commit()

