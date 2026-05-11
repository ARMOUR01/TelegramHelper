"""Регистрация меню команд (видно при наборе `/` в чате с ботом).
Подсказки выскакивают в любом клиенте Telegram: iOS/Android/Desktop/Web.
"""
import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

from src.config import settings


logger = logging.getLogger(__name__)


# Порядок важен — это порядок показа в выпадающем меню.
OWNER_COMMANDS: list[BotCommand] = [
    BotCommand(command="settings", description="⚙ Настройки"),
    BotCommand(command="digest", description="☀ Утренний дайджест"),
    BotCommand(command="all_summary", description="📋 Выжимка по всем чатам"),
    BotCommand(command="chat", description="💬 Действия по чату"),
    BotCommand(command="catchup", description="⏪ Где мы остановились"),
    BotCommand(command="send", description="✉ Отправить сообщение"),
    BotCommand(command="search", description="🔎 Поиск по сообщениям"),
    BotCommand(command="todos", description="✅ Открытые обещания"),
    BotCommand(command="news", description="📰 Новости по теме"),
    BotCommand(command="news_topics", description="🗂 Темы авто-новостей"),
    BotCommand(command="news_channels", description="📡 Каналы-источники"),
    BotCommand(command="sync", description="🔄 Обновить контакты"),
    BotCommand(command="style", description="🎨 Пересчёт стиля"),
    BotCommand(command="index", description="📚 Индексация чата"),
    BotCommand(command="login", description="🔐 Подключить Telegram"),
    BotCommand(command="logout", description="🚪 Отключить аккаунт"),
    BotCommand(command="help", description="ℹ Помощь"),
]


async def setup_bot_commands(bot: Bot) -> None:
    """Регистрирует меню команд персонально для владельца (scope=chat).
    Бот owner-only, поэтому другим пользователям меню видеть не нужно."""
    try:
        await bot.set_my_commands(
            OWNER_COMMANDS,
            scope=BotCommandScopeChat(chat_id=settings.owner_telegram_id),
        )
        logger.info("Bot command menu registered (%d items)", len(OWNER_COMMANDS))
    except Exception:
        logger.exception("Failed to register bot command menu")
