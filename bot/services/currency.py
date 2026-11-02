"""Курс доллара ЦБ Узбекистана (cbu.uz), кешируется в базе на день."""
import logging
from datetime import date
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Rate

log = logging.getLogger(__name__)
CBU_URL = "https://cbu.uz/ru/arkhiv-kursov-valyut/json/{cur}/"
FALLBACK_USD = Decimal("12000")


async def get_rate(s: AsyncSession, currency: str, today: date) -> Decimal:
    """Сколько сумов стоит 1 единица валюты."""
    if currency == "UZS":
        return Decimal(1)
    cached = await s.scalar(select(Rate).where(Rate.currency == currency, Rate.day == today))
    if cached:
        return cached.rate
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(CBU_URL.format(cur=currency))
            r.raise_for_status()
            value = Decimal(str(r.json()[0]["Rate"]))
        s.add(Rate(currency=currency, day=today, rate=value))
        await s.commit()
        return value
    except Exception as e:  # сеть или формат ответа — берём последний известный курс
        log.warning("Не удалось получить курс %s: %s", currency, e)
        last = await s.scalar(select(Rate).where(Rate.currency == currency).order_by(Rate.day.desc()))
        return last.rate if last else FALLBACK_USD


async def to_uzs(s: AsyncSession, amount: Decimal, currency: str, today: date) -> Decimal:
    return amount * await get_rate(s, currency, today)
