"""Регистрация меню команд (видно при наборе `/` в чате с ботом).
Подсказки выскакивают в любом клиенте Telegram: iOS/Android/Desktop/Web.
"""
import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

from src.config import settings


logger = logging.getLogger(__name__)


# Порядок важен — это порядок показа в выпадающем меню. Описание видно сразу
# под именем команды, поэтому делаем их информативными (≤ 256 симв на пункт).
OWNER_COMMANDS: list[BotCommand] = [
    # Сводки
    BotCommand(command="digest",      description="☀ Утренний дайджест (ждут / горят / авто-ответы)"),
    BotCommand(command="all_summary", description="📋 Выжимка по топ-N личным чатам за период"),
    BotCommand(command="meetings",    description="🗓 Запланированные встречи и дедлайны по чатам"),
    BotCommand(command="catchup",     description="⏪ Где мы остановились в чате + черновик ответа"),
    BotCommand(command="chat",        description="💬 Меню действий по чату: саммари / задачи / черновик"),
    # Сталкер-пак
    BotCommand(command="poi",         description="⭐ POI: важные контакты под глубоким наблюдением"),
    BotCommand(command="profile",     description="📇 Досье на контакта по истории (Gemini)"),
    BotCommand(command="rel",         description="📊 Статистика отношений с контактом"),
    BotCommand(command="mutual",      description="🤝 Взаимный контакт? Удалил ли тебя?"),
    BotCommand(command="visitors",    description="👀 Кто чаще всего заходит к тебе в чат"),
    BotCommand(command="activity",    description="📈 Heatmap активности POI (когда онлайн)"),
    BotCommand(command="deletions",   description="🗑 Удалённые «для всех» сообщения с текстом"),
    # Действия
    BotCommand(command="send",        description="✉ Подготовить сообщение от твоего имени"),
    BotCommand(command="del",         description="🗑 Удалить моё последнее сообщение в чате"),
    BotCommand(command="search",      description="🔎 Поиск по всей истории сообщений"),
    BotCommand(command="todos",       description="✅ Открытые обещания и их дедлайны"),
    # AI takeover
    BotCommand(command="ai_on",       description="🤖 AI ведёт чат за меня (per-contact)"),
    BotCommand(command="ai_off",      description="🔴 Выключить AI на контакте"),
    BotCommand(command="ai_list",     description="📋 На ком сейчас включён AI"),
    # Новости
    BotCommand(command="news",          description="📰 Дайджест по теме за период"),
    BotCommand(command="news_topics",   description="🗂 Темы для авто-новостей по утрам"),
    BotCommand(command="news_channels", description="📡 Какие каналы — источники для /news"),
    # Сервис
    BotCommand(command="sync",     description="🔄 Подгрузить контакты и архив из Telegram"),
    BotCommand(command="style",    description="🎨 Пересчитать твой стиль письма из истории"),
    BotCommand(command="index",    description="📚 Векторная индексация чата для семантики"),
    BotCommand(command="settings", description="⚙ Все настройки: tz, провайдер, ключи"),
    BotCommand(command="login",    description="🔐 Подключить Telegram-аккаунт"),
    BotCommand(command="logout",   description="🚪 Отключить аккаунт"),
    BotCommand(command="stop",     description="🛑 Прервать текущую операцию"),
    BotCommand(command="help",     description="ℹ Полная справка по командам"),
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
