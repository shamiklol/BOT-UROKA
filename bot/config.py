from functools import cached_property
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    # Telegram ID владельца. Пусто — бот подскажет ваш ID в ответ на /start.
    owner_id: int | None = None
    database_url: str = "sqlite+aiosqlite:///bot.db"
    tz: str = "Asia/Tashkent"

    # ИИ (необязательно). Без ключа бот понимает простые фразы по правилам.
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5-5"
    # Голосовые (необязательно): ключ OpenAI для распознавания речи (Whisper).
    openai_api_key: str | None = None

    @cached_property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def db_url(self) -> str:
        url = self.database_url
        # Railway отдаёт postgres:// или postgresql:// — переводим на асинхронный драйвер.
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        return url

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()  # type: ignore[call-arg]
