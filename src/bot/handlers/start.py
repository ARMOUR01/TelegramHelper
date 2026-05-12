from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.db.repo import get_or_create_user
from src.db.session import get_session


router = Router(name="start")
router.message.filter(OwnerOnly())


WELCOME = (
    "👋 Я твой AI-ассистент для Telegram. Со мной можно говорить <b>свободным текстом</b> "
    "(«напомни маме в 18:00», «саммари с Олей», «дай выжимку по чатам») или командами. "
    "Меню всех команд — набери <code>/</code> в чате.\n\n"
    "Полная справка: /help"
)


HELP_TEXT = (
    "📘 <b>Команды</b>\n\n"

    "<b>📊 Сводки</b>\n"
    "/digest <code>[now|on|off|at HH:MM]</code> — утренний дайджест: ждут / горят / авто-ответы\n"
    "/all_summary <code>[top_n=10] [часов=24]</code> — выжимка по топ-N активным личным чатам\n"
    "/meetings <code>[часов=168]</code> — запланированные встречи / звонки / дедлайны из всех чатов\n"
    "/catchup <code>имя</code> — «где мы остановились» + черновик ответа\n"
    "/chat <code>имя</code> — меню действий: саммари / задачи / черновик / catchup\n\n"

    "<b>⭐ Сталкер-пак (personal CRM)</b>\n"
    "/poi <code>list | add имя | remove имя</code> — отметка важных контактов под глубокое наблюдение\n"
    "/profile <code>имя [--refresh]</code> — досье из истории (Gemini): интересы, важные люди, тон, открытые темы\n"
    "/rel <code>имя [дней=30]</code> — кто чаще пишет, среднее время ответа, активность\n"
    "/mutual <code>имя</code> — взаимный ли контакт; вероятно ли что удалил тебя\n"
    "/visitors <code>[часов=168] [top_n=20]</code> — кто чаще всего «заходит» к тебе в чат "
    "(прочёл / печатал / написал)\n"
    "/activity <code>имя [дней=14]</code> — heatmap по часам — когда POI обычно онлайн\n"
    "/deletions <code>[часов=72]</code> — удалённые «для всех» сообщения с восстановленным текстом\n\n"

    "<b>💼 Действия</b>\n"
    "/send <code>инструкция</code> — «скажи Оле что созвон в 8» (с подтверждением)\n"
    "/del <code>имя [количество] [подстрока]</code> — удалить мои последние сообщения в чате (revoke)\n"
    "/search <code>текст</code> — поиск по всей истории сообщений\n"
    "/todos — открытые обещания (мои и мне)\n\n"

    "<b>📰 Новости</b>\n"
    "/news <code>тема [--hours=24]</code> — дайджест из каналов-источников\n"
    "/news_topics — темы для авто-новостей по утрам\n"
    "/news_channels — какие каналы являются источниками\n\n"

    "<b>⚙ Сервис</b>\n"
    "/settings — все настройки: tz, провайдер, ключи, авто-ответ\n"
    "/sync — подгрузить контакты и архивы из Telegram\n"
    "/style <code>имя</code> — пересчитать твой стиль общения с этим контактом\n"
    "/index <code>имя</code> — векторная индексация чата для семантического поиска\n"
    "/login — подключить Telegram-аккаунт\n"
    "/logout — отключить аккаунт\n"
    "/stop (или /cancel) — прервать текущую долгую операцию\n\n"

    "<b>💬 Свободный текст</b>\n"
    "Можно просто писать: «включи дайджест в 10», «напомни в 18 завтра», "
    "«саммари с Олей», «дай выжимку по чатам за сутки», «следи за Артёмом»,\n"
    "«отправь Маше что буду позже», «удали моё последнее в чате с Артёмом», "
    "«покажи встречи на неделю» и т.п."
    "\n\n<b>📸 Медиа-мозги</b>\n"
    "Голосовые и видеокружочки расшифровываются автоматически. Для описания "
    "фото и кадров видео включи Vision в /settings (поддерживается через Gemini)."
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id)
    await message.answer(WELCOME)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)
