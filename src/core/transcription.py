import asyncio
import logging
import os
from pathlib import Path

from src.db.repo import cache_transcript, get_cached_transcript
from src.db.session import get_session


logger = logging.getLogger(__name__)


# Размер локальной whisper-модели можно подменить через env (tiny | base | small | medium | large-v3)
# Для деплоя в окружения с малой RAM (Fly.io free, ~512MB) рекомендуется `tiny` (75 MB)
# или `base` (~150 MB). По умолчанию используем `small` для качества.
_DEFAULT_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small").strip() or "small"


class TranscriptionService:
    """Локальный faster-whisper / OpenAI Whisper API / hybrid (local с fallback в API)."""

    def __init__(self, model_size: str = _DEFAULT_MODEL_SIZE) -> None:
        self._model_size = model_size
        self._model = None
        self._lock = asyncio.Lock()

    async def _ensure_local_model(self) -> object:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel  # тяжёлый импорт держим ленивым

                def _load() -> object:
                    return WhisperModel(self._model_size, device="auto", compute_type="auto")

                self._model = await asyncio.to_thread(_load)
        return self._model

    async def _transcribe_local(self, path: Path, language: str | None) -> str:
        model = await self._ensure_local_model()

        def _run() -> str:
            segments, _info = model.transcribe(str(path), language=language)
            return " ".join(seg.text.strip() for seg in segments).strip()

        return await asyncio.to_thread(_run)

    async def _transcribe_api(self, path: Path, openai_key: str, language: str | None) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=openai_key)
        with path.open("rb") as f:
            resp = await client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=language,
            )
        return resp.text

    async def transcribe(
        self,
        path: Path,
        *,
        file_id: str | None = None,
        mode: str = "hybrid",
        openai_key: str | None = None,
        language: str | None = None,
    ) -> str:
        # file_id используется как ключ кэша — обычно telegram media file_unique_id
        if file_id:
            async with get_session() as session:
                cached = await get_cached_transcript(session, file_id)
                if cached:
                    return cached

        text = ""
        if mode == "api":
            if not openai_key:
                raise ValueError("OpenAI API key required for transcription mode='api'")
            text = await self._transcribe_api(path, openai_key, language)
        elif mode == "local":
            text = await self._transcribe_local(path, language)
        else:  # hybrid
            try:
                text = await self._transcribe_local(path, language)
            except Exception:
                logger.exception("Local transcription failed, falling back to API")
                if openai_key:
                    text = await self._transcribe_api(path, openai_key, language)
                else:
                    raise

        if file_id and text:
            async with get_session() as session:
                await cache_transcript(session, file_id, text)
        return text


transcription_service = TranscriptionService()
