"""Классификация и обработка медиа-сообщений: video, video_note, sticker, gif,
а также «мозги» — извлечение аудиодорожки из видео и vision-описание фото.

Все downstream-фичи (digest, all_summary, dossier, /chat) автоматически видят
эти данные, потому что `message_to_text(...)` сам подставляет
`transcript` / `extracted_text` если они есть."""
from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

from telethon.tl.custom import Message as TgMessage


logger = logging.getLogger(__name__)


def classify(msg: TgMessage) -> str:
    """Возвращает один из:
    text | voice | audio | video | video_note | sticker | gif | photo | document | other.

    Порядок важен: video_note нужно проверять ДО video (кружочек — это тоже видео).
    """
    if getattr(msg, "voice", None):
        return "voice"
    if getattr(msg, "audio", None):
        return "audio"
    if getattr(msg, "video_note", None):
        return "video_note"
    if getattr(msg, "sticker", None):
        return "sticker"
    if getattr(msg, "gif", None):
        return "gif"
    if getattr(msg, "video", None):
        return "video"
    if getattr(msg, "photo", None):
        return "photo"
    if getattr(msg, "document", None):
        return "document"
    if msg.text:
        return "text"
    return "other"


async def extract_audio(video_path: Path, out_path: Path | None = None) -> Path | None:
    """ffmpeg: видео → mono 16kHz ogg, чтобы whisper съел без лишних мегабайт."""
    if out_path is None:
        out_path = video_path.with_suffix(".audio.ogg")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libopus", "-b:a", "32k",
        str(out_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("ffmpeg failed (%s): %s", proc.returncode, (err or b"")[:200])
            return None
        if not out_path.exists() or out_path.stat().st_size == 0:
            return None
        return out_path
    except FileNotFoundError:
        logger.warning("ffmpeg not installed — cannot extract audio from video")
        return None
    except Exception:
        logger.exception("extract_audio failed")
        return None


async def sample_video_frames(video_path: Path, out_dir: Path, *, count: int = 3) -> list[Path]:
    """Извлекает N равноотстоящих кадров из видео через ffmpeg для последующего vision."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / f"{video_path.stem}_frame_%02d.jpg")
    # fps-фильтр: count кадров по всей длительности → fps = count/duration. Проще через
    # `select='not(mod(n,N))'` + ограничение -frames, но самое надёжное —
    # `thumbnail` сэмплер: одно лицо на 100 кадров. Делаем поэтапно: count=3 фиксированных моментов.
    duration = await _probe_duration(video_path)
    if duration is None or duration <= 0:
        return []
    paths: list[Path] = []
    for i in range(count):
        ts = duration * (i + 1) / (count + 1)  # 1/(N+1), 2/(N+1) ... — равномерно
        frame_path = out_dir / f"{video_path.stem}_frame_{i:02d}.jpg"
        cmd = [
            "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "5", str(frame_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if frame_path.exists() and frame_path.stat().st_size > 0:
                paths.append(frame_path)
        except Exception:
            logger.exception("sample_video_frames failed at %s", ts)
    return paths


async def _probe_duration(path: Path) -> float | None:
    """ffprobe: длительность в секундах. Возвращает None если ffprobe нет / не вышло."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return float(out.decode().strip())
    except Exception:
        return None


def file_to_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")
