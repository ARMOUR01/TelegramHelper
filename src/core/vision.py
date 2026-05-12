"""Vision: описание картинок (и кадров видео) через мультимодальные LLM.

Сейчас работает только Gemini (нативная мультимодальность). Для OpenAI можно
добавить отдельную ветку позже, но картинка-стоимость у Gemini Flash почти
нулевая — это оправданный default."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

from src.config import LLMDefaults
from src.llm.base import LLMProvider
from src.llm.gemini_provider import GeminiProvider


logger = logging.getLogger(__name__)


CAPTION_PROMPT = (
    "Опиши, что на этом изображении, КРАТКО и ПО ДЕЛУ (1-3 предложения). "
    "Если на картинке текст (скриншот переписки/документа/мема/чека/билета) — "
    "ПОЛНОСТЬЮ извлеки его сохраняя осмысленное форматирование. "
    "Если это люди или объекты — кратко опиши кто/что, эмоция и контекст. "
    "Если это мем — попытайся объяснить смысл. "
    "Никаких префиксов «На картинке...» — сразу к сути, без воды."
)


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "image/jpeg"


async def describe_image(provider: LLMProvider, image_path: Path) -> str | None:
    """Caption + OCR одного изображения. None если провайдер не поддерживает vision
    или произошла ошибка."""
    if not isinstance(provider, GeminiProvider):
        logger.debug("vision: provider %s not supported, skipping", provider.name)
        return None
    if not image_path.exists() or image_path.stat().st_size == 0:
        return None

    mime = _guess_mime(image_path)
    data = image_path.read_bytes()

    def _call() -> str:
        try:
            # google-genai inline_data format
            response = provider._client.models.generate_content(  # noqa: SLF001
                model=LLMDefaults.GEMINI_CHAT_LIGHT,
                contents=[
                    {"role": "user", "parts": [
                        {"inline_data": {"mime_type": mime, "data": data}},
                        {"text": CAPTION_PROMPT},
                    ]},
                ],
            )
            return (response.text or "").strip()
        except Exception:
            logger.exception("Gemini vision call failed")
            return ""

    text = await asyncio.to_thread(_call)
    return text or None


async def describe_video_frames(
    provider: LLMProvider, frame_paths: list[Path]
) -> str | None:
    """Multi-frame vision: пачка кадров → одно общее описание видео."""
    if not isinstance(provider, GeminiProvider):
        return None
    valid_frames = [p for p in frame_paths if p.exists() and p.stat().st_size > 0]
    if not valid_frames:
        return None

    parts: list[dict] = []
    for p in valid_frames:
        parts.append({"inline_data": {"mime_type": _guess_mime(p), "data": p.read_bytes()}})
    parts.append({"text":
        "Это N кадров из одного видео, идут по порядку. Опиши КРАТКО (2-4 предложения) "
        "что происходит на видео в целом: действие, обстановка, ключевой смысл. "
        "Если на кадрах есть текст/субтитры — извлеки. Без преамбул."
    })

    def _call() -> str:
        try:
            response = provider._client.models.generate_content(  # noqa: SLF001
                model=LLMDefaults.GEMINI_CHAT_LIGHT,
                contents=[{"role": "user", "parts": parts}],
            )
            return (response.text or "").strip()
        except Exception:
            logger.exception("Gemini multi-frame vision failed")
            return ""

    text = await asyncio.to_thread(_call)
    return text or None
