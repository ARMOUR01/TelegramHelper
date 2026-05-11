"""Хелпер для индикатора «assistant печатает…» в Telegram во время долгих
операций (LLM-вызовы, /all_summary, чат-экшены и т.п.). Использование:

    from src.bot.typing import typing

    async with typing(message):
        result = await some_long_call()
        await message.answer(result)
"""
from contextlib import asynccontextmanager

from aiogram.utils.chat_action import ChatActionSender


@asynccontextmanager
async def typing(event):
    """Контекстный менеджер: пока работает блок, в Telegram видно
    «<bot> is typing…». Поддерживает Message и CallbackQuery.
    Действие автоматически прекращается через 5 секунд после выхода из блока
    (Telegram API ограничение), поэтому ChatActionSender внутри сам обновляет
    его каждые ~5 секунд."""
    if getattr(event, "chat", None) is not None:
        chat_id = event.chat.id
    elif getattr(event, "message", None) is not None and event.message is not None:
        chat_id = event.message.chat.id
    else:
        # некому слать индикатор — просто пропустим
        yield
        return
    bot = event.bot
    if bot is None:
        yield
        return
    async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
        yield
