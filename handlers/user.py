from datetime import datetime
from telegram import Update, ChatMember
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import CHANNEL_ID, ADMIN_IDS
from utils.keyboards import Keyboards
from utils.database import Database
from models.user import User

db = Database()

async def check_channel_subscription(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Проверка подписки пользователя на канал"""
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except TelegramError as e:
        print(f"Ошибка проверки подписки для пользователя {user_id}: {e}")
        return False

async def show_channel_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать экран проверки подписки на канал"""
    keyboard = Keyboards.channel_check()
    message_text = "🔒 Для использования бота необходимо подписаться на наш канал"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=message_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            text=message_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    ref = context.args[0] if context.args else None

    # Получаем или создаем пользователя
    user = db.get_or_create_user(user_id)
    
    # Проверяем подписку на канал (кроме админов)
    if not user_id in ADMIN_IDS:
        is_subscribed = await check_channel_subscription(context, user_id)
        if not is_subscribed:
            await show_channel_check(update, context)
            return

    # Обрабатываем реферальную ссылку
    if ref and ref.isdigit():
        ref_id = int(ref)
        if ref_id != user_id:
            db.add_referral(ref_id, user_id)

    # Показываем главное меню
    keyboard = Keyboards.main_menu(is_admin=(user_id in ADMIN_IDS))
    welcome_text = f"""🌟 *Добро пожаловать, {user_name}!*

💎 Вы в самом крутом заработок-боте Telegram!

🚀 *Что вас ждет:*
• 💰 Зарабатывайте приглашая друзей
• 🎁 Получайте ежедневные бонусы
• 💸 Выводите реальные деньги
• 🏆 Участвуйте в рейтинге
• 📈 Инвестируйте и получайте прибыль

✨ Начните зарабатывать прямо сейчас!"""

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=welcome_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            text=welcome_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data

    # Проверяем наличие пользователя
    user = db.get_user(user_id)
    if not user:
        await start(update, context)
        return

    # Проверяем подписку на канал (кроме админов)
    if not user_id in ADMIN_IDS:
        is_subscribed = await check_channel_subscription(context, user_id)
        if not is_subscribed and data != 'check_subscription':
            await show_channel_check(update, context)
            return

    # Обрабатываем различные команды
    if data == 'menu':
        await start(update, context)
    elif data == 'check_subscription':
        is_subscribed = await check_channel_subscription(context, user_id)
        if is_subscribed:
            await start(update, context)
        else:
            await query.answer("❌ Подписка не найдена", show_alert=True)
            await show_channel_check(update, context)
    # Остальные команды обрабатываются в соответствующих модулях