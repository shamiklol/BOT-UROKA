"""Распознавание голосовых через OpenAI Whisper (если задан OPENAI_API_KEY)."""
import logging

import httpx

from bot.config import settings

log = logging.getLogger(__name__)


async def transcribe(audio: bytes, filename: str = "voice.ogg") -> str | None:
    if not settings.openai_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                files={"file": (filename, audio, "audio/ogg")},
                data={"model": "whisper-1"},
            )
            r.raise_for_status()
            return (r.json().get("text") or "").strip() or None
    except Exception as e:
        log.warning("Не удалось распознать голос: %s", e)
        return None
