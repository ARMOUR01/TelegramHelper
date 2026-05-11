"""/all_summary — выжимка по топ-N активным личным чатам за окно времени."""
import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.bot.task_registry import cancellable
from src.bot.typing import typing
from src.core.all_chats_summary import (
    DEFAULT_HOURS,
    DEFAULT_TOP_N,
    MAX_HOURS,
    MAX_TOP_N,
    build_all_chats_summary,
)


logger = logging.getLogger(__name__)
router = Router(name="all_summary_cmd")
router.message.filter(OwnerOnly())


USAGE = (
    "Формат: <code>/all_summary [top_n] [часов]</code>\n"
    "Примеры:\n"
    "  <code>/all_summary</code> — топ-10 за 24ч\n"
    "  <code>/all_summary 5</code> — топ-5 за 24ч\n"
    "  <code>/all_summary 5 12</code> — топ-5 за 12ч\n"
    f"Ограничения: top_n ≤ {MAX_TOP_N}, часов ≤ {MAX_HOURS}."
)


@router.message(Command("all_summary", "all"))
async def cmd_all_summary(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    top_n = DEFAULT_TOP_N
    hours = DEFAULT_HOURS
    try:
        if len(args) >= 1:
            top_n = int(args[0])
        if len(args) >= 2:
            hours = int(args[1])
    except ValueError:
        await message.answer(USAGE)
        return

    top_n = max(1, min(MAX_TOP_N, top_n))
    hours = max(1, min(MAX_HOURS, hours))

    notice = await message.answer(
        f"⏳ Собираю выжимку по топ-{top_n} чатам за {hours}ч…"
    )
    try:
        async with cancellable(message.from_user.id), typing(message):
            parts = await build_all_chats_summary(
                message.from_user.id, top_n=top_n, hours=hours,
            )
    except asyncio.CancelledError:
        try:
            await notice.edit_text("🛑 Отменено.")
        except Exception:
            pass
        return
    except Exception:
        logger.exception("all_summary failed")
        try:
            await notice.edit_text("❌ Что-то пошло не так при сборе выжимки.")
        except Exception:
            await message.answer("❌ Что-то пошло не так при сборе выжимки.")
        return

    if not parts:
        await notice.edit_text("Пусто.")
        return

    first, *rest = parts
    try:
        await notice.edit_text(first, disable_web_page_preview=True)
    except Exception:
        await message.answer(first, disable_web_page_preview=True)
    for chunk in rest:
        await message.answer(chunk, disable_web_page_preview=True)
