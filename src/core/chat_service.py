import logging
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.custom import Message as TgMessage

from sqlalchemy import select

from src.config import settings as app_settings
from src.core.documents import extract_text, is_supported
from src.core.media import classify as _classify_media, extract_audio, sample_video_frames
from src.core.transcription import transcription_service
from src.core.vision import describe_image, describe_video_frames
from src.db.models import Message, User, UserSettings
from src.db.repo import (
    fetch_chat_messages,
    get_api_key,
    get_or_create_user,
    upsert_message,
)
from src.db.session import get_session
from src.llm.router import build_provider


logger = logging.getLogger(__name__)


def _media_dir(owner_telegram_id: int) -> Path:
    path = app_settings.data_dir / "media" / str(owner_telegram_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _classify(msg: TgMessage) -> str:
    return _classify_media(msg)


def _peer_id_from_message(msg: TgMessage) -> int:
    """Возвращает каноничный peer_id чата."""
    chat = msg.chat
    if chat is not None:
        return chat.id
    return msg.peer_id.user_id if hasattr(msg.peer_id, "user_id") else msg.chat_id


async def _sender_label(msg: TgMessage) -> str | None:
    sender = await msg.get_sender() if msg.sender_id else None
    if sender is None:
        return None
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(p for p in parts if p).strip()
    if name:
        return name
    return getattr(sender, "username", None) or str(sender.id)


async def _process_one(
    client: TelegramClient,
    owner: User,
    msg: TgMessage,
    *,
    media_root: Path,
    transcribe: bool,
    parse_docs: bool,
    openai_key: str | None,
    transcription_mode: str,
    vision_enabled: bool = False,
    video_vision_enabled: bool = False,
) -> None:
    kind = _classify(msg)
    peer_id = _peer_id_from_message(msg)
    sender_name = await _sender_label(msg)
    text = msg.text or msg.message or None
    transcript: str | None = None
    extracted: str | None = None
    media_path: str | None = None

    if kind in {"voice", "audio"} and transcribe:
        try:
            target = media_root / f"{peer_id}_{msg.id}.ogg"
            await msg.download_media(file=str(target))
            media_path = str(target)
            file_id = str(getattr(msg.file, "id", None) or f"{peer_id}:{msg.id}")
            transcript = await transcription_service.transcribe(
                target,
                file_id=file_id,
                mode=transcription_mode,
                openai_key=openai_key,
            )
        except Exception:
            logger.exception("transcription failed for msg %s", msg.id)

    elif kind in {"video", "video_note"} and transcribe:
        try:
            ext = ".mp4" if kind == "video" else ".mov"
            target = media_root / f"{peer_id}_{msg.id}{ext}"
            await msg.download_media(file=str(target))
            media_path = str(target)
            audio = await extract_audio(target)
            if audio is not None:
                file_id = str(getattr(msg.file, "id", None) or f"{peer_id}:{msg.id}")
                transcript = await transcription_service.transcribe(
                    audio,
                    file_id=file_id,
                    mode=transcription_mode,
                    openai_key=openai_key,
                )
            # Опциональный vision по ключевым кадрам только если включено в /settings
            if video_vision_enabled and kind == "video":
                try:
                    frames_dir = media_root / "frames"
                    frames = await sample_video_frames(target, frames_dir, count=3)
                    if frames:
                        async with get_session() as s2:
                            owner_fresh = await get_or_create_user(s2, owner.telegram_id)
                            provider = await build_provider(s2, owner_fresh)
                        if provider is not None:
                            caption = await describe_video_frames(provider, frames)
                            if caption:
                                # Кладём в extracted_text — он попадает в LLM-контекст
                                # как обычный текст наряду с transcript.
                                extracted = caption
                except Exception:
                    logger.exception("video vision failed for msg %s", msg.id)
        except Exception:
            logger.exception("video transcription failed for msg %s", msg.id)

    elif kind == "photo" and vision_enabled:
        try:
            target = media_root / f"{peer_id}_{msg.id}.jpg"
            await msg.download_media(file=str(target))
            media_path = str(target)
            async with get_session() as s2:
                owner_fresh = await get_or_create_user(s2, owner.telegram_id)
                provider = await build_provider(s2, owner_fresh)
            if provider is not None:
                caption = await describe_image(provider, target)
                if caption:
                    extracted = caption
        except Exception:
            logger.exception("photo vision failed for msg %s", msg.id)

    elif kind == "document" and parse_docs:
        filename = getattr(msg.file, "name", None) or f"{msg.id}.bin"
        if is_supported(filename):
            try:
                target = media_root / f"{peer_id}_{msg.id}_{filename}"
                await msg.download_media(file=str(target))
                media_path = str(target)
                extracted = await extract_text(target)
            except Exception:
                logger.exception("doc parse failed for msg %s", msg.id)

    async with get_session() as session:
        await upsert_message(
            session,
            user_id=owner.id,
            peer_id=peer_id,
            message_id=msg.id,
            sender_id=msg.sender_id,
            sender_name=sender_name,
            is_outgoing=bool(msg.out),
            date=msg.date.replace(tzinfo=None) if msg.date else datetime.utcnow(),
            kind=kind,
            text=text,
            transcript=transcript,
            media_path=media_path,
            extracted_text=extracted,
        )


async def _last_cached_message_id(owner_id: int, peer_id: int) -> int:
    async with get_session() as session:
        result = await session.execute(
            select(Message.message_id)
            .where(Message.user_id == owner_id, Message.peer_id == peer_id)
            .order_by(Message.message_id.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return int(row or 0)


async def _cached_count(owner_id: int, peer_id: int) -> int:
    from sqlalchemy import func
    async with get_session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.user_id == owner_id, Message.peer_id == peer_id)
        )
        return int(result.scalar_one() or 0)


async def load_chat(
    client: TelegramClient,
    owner_telegram_id: int,
    peer_id: int,
    *,
    limit: int = 50,
    transcribe: bool = True,
    parse_docs: bool = False,
    incremental: bool = True,
) -> list[Message]:
    # incremental: если в БД достаточно сообщений, тянем только новее последнего
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        s: UserSettings = owner.settings
        openai_key = await get_api_key(session, owner, "openai")
        transcription_mode = s.transcription_mode

    media_root = _media_dir(owner_telegram_id)
    entity = await client.get_entity(peer_id)

    iter_kwargs = {"limit": limit}
    if incremental:
        cached_total = await _cached_count(owner.id, peer_id)
        if cached_total >= limit:
            last_id = await _last_cached_message_id(owner.id, peer_id)
            if last_id:
                iter_kwargs = {"min_id": last_id, "limit": None}

    async for msg in client.iter_messages(entity, **iter_kwargs):
        await _process_one(
            client,
            owner,
            msg,
            media_root=media_root,
            transcribe=transcribe,
            parse_docs=parse_docs,
            openai_key=openai_key,
            transcription_mode=transcription_mode,
            vision_enabled=bool(getattr(s, "vision_enabled", False)),
            video_vision_enabled=bool(getattr(s, "video_vision_enabled", False)),
        )

    if transcribe:
        await _backfill_transcripts(
            client, owner.id, owner_telegram_id, peer_id,
            limit=limit, media_root=media_root,
            openai_key=openai_key, transcription_mode=transcription_mode,
            vision_enabled=bool(getattr(s, "vision_enabled", False)),
            video_vision_enabled=bool(getattr(s, "video_vision_enabled", False)),
        )

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        return await fetch_chat_messages(session, owner, peer_id, limit=limit)


async def _backfill_transcripts(
    client: TelegramClient,
    owner_id: int,
    owner_telegram_id: int,
    peer_id: int,
    *,
    limit: int,
    media_root: Path,
    openai_key: str | None,
    transcription_mode: str,
    vision_enabled: bool = False,
    video_vision_enabled: bool = False,
) -> None:
    # mirror кладёт voice/audio/video/video_note/photo без transcript — догоняем ленически.
    pending_kinds: list[str] = ["voice", "audio", "video", "video_note"]
    if vision_enabled:
        pending_kinds.append("photo")

    async with get_session() as session:
        result = await session.execute(
            select(Message)
            .where(
                Message.user_id == owner_id,
                Message.peer_id == peer_id,
                Message.kind.in_(tuple(pending_kinds)),
                Message.transcript.is_(None),
                Message.extracted_text.is_(None),
            )
            .order_by(Message.date.desc())
            .limit(limit)
        )
        pending = list(result.scalars().all())

    if not pending:
        return

    for m in pending:
        try:
            tg_msg = await client.get_messages(peer_id, ids=m.message_id)
            if tg_msg is None:
                continue
            transcript: str | None = None
            extracted: str | None = None
            target: Path | None = None

            if m.kind in ("voice", "audio"):
                target = media_root / f"{peer_id}_{m.message_id}.ogg"
                await tg_msg.download_media(file=str(target))
                file_id = str(getattr(tg_msg.file, "id", None) or f"{peer_id}:{m.message_id}")
                transcript = await transcription_service.transcribe(
                    target,
                    file_id=file_id,
                    mode=transcription_mode,
                    openai_key=openai_key,
                )
            elif m.kind in ("video", "video_note"):
                ext = ".mp4" if m.kind == "video" else ".mov"
                target = media_root / f"{peer_id}_{m.message_id}{ext}"
                await tg_msg.download_media(file=str(target))
                audio = await extract_audio(target)
                if audio is not None:
                    file_id = str(getattr(tg_msg.file, "id", None) or f"{peer_id}:{m.message_id}")
                    transcript = await transcription_service.transcribe(
                        audio,
                        file_id=file_id,
                        mode=transcription_mode,
                        openai_key=openai_key,
                    )
                if video_vision_enabled and m.kind == "video":
                    frames_dir = media_root / "frames"
                    frames = await sample_video_frames(target, frames_dir, count=3)
                    if frames:
                        async with get_session() as s2:
                            owner_fresh = await get_or_create_user(s2, owner_telegram_id)
                            provider = await build_provider(s2, owner_fresh)
                        if provider is not None:
                            caption = await describe_video_frames(provider, frames)
                            if caption:
                                extracted = caption
            elif m.kind == "photo" and vision_enabled:
                target = media_root / f"{peer_id}_{m.message_id}.jpg"
                await tg_msg.download_media(file=str(target))
                async with get_session() as s2:
                    owner_fresh = await get_or_create_user(s2, owner_telegram_id)
                    provider = await build_provider(s2, owner_fresh)
                if provider is not None:
                    caption = await describe_image(provider, target)
                    if caption:
                        extracted = caption
        except Exception:
            logger.exception("backfill failed for msg %s in peer %s", m.message_id, peer_id)
            continue

        if not transcript and not extracted:
            continue
        async with get_session() as session:
            await upsert_message(
                session,
                user_id=owner_id, peer_id=peer_id, message_id=m.message_id,
                sender_id=m.sender_id, sender_name=m.sender_name,
                is_outgoing=m.is_outgoing, date=m.date,
                kind=m.kind, text=m.text,
                transcript=transcript or m.transcript,
                media_path=str(target) if target else m.media_path,
                extracted_text=extracted or m.extracted_text,
            )


_KIND_PLACEHOLDERS = {
    "voice": "[голосовое — не расшифровано]",
    "audio": "[аудио — не расшифровано]",
    "video": "[видео — без расшифровки/описания]",
    "video_note": "[видеокружок — не расшифровано]",
    "photo": "[фото — описание выключено в /settings]",
    "sticker": "[стикер]",
    "gif": "[GIF]",
    "document": "[документ]",
    "other": "[медиа]",
}


def message_to_text(m: Message) -> str:
    """Превращает Message в строку для LLM-промта.
    Подключает transcript / extracted_text если есть; для медиа без расшифровки
    кладёт человекочитаемый плейсхолдер, чтобы LLM понимал что это.
    """
    parts: list[str] = []
    if m.text:
        parts.append(m.text)
    if m.transcript:
        prefix = "🎙" if m.kind in ("voice", "audio", "video_note") else "🎞"
        parts.append(f"{prefix} {m.transcript}")
    if m.extracted_text:
        prefix = "🖼" if m.kind in ("photo", "video") else "📄"
        parts.append(f"{prefix} {m.extracted_text}")
    if not parts:
        parts.append(_KIND_PLACEHOLDERS.get(m.kind, f"[{m.kind}]"))
    body = "  ".join(parts)
    who = "Я" if m.is_outgoing else (m.sender_name or "Они")
    when = m.date.strftime("%Y-%m-%d %H:%M")
    return f"[{when}] {who}: {body}"


def messages_to_transcript(messages: list[Message]) -> str:
    return "\n".join(message_to_text(m) for m in messages)
