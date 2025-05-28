import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config.settings import REFERRAL_BONUS
from utils.keyboards import Keyboards
from utils.database import Database
from utils.helpers import format_currency, plural_form

# ... existing code ...

db = Database()

async def show_referral_program(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать реферальную статистику и ссылку"""
    query = update.callback_query
    user_id = query.from_user.id
    
    bot_username = context.bot.username
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
    
    # Получаем статистику рефералов
    referrals = db.get_user_referrals(user_id)
    total_earned = len(referrals) * REFERRAL_BONUS
    
    ref_text = f"""👥 *Реферальная программа*

💰 Приглашайте друзей и получайте *{format_currency(REFERRAL_BONUS)}* за каждого!

🔗 *Ваша реферальная ссылка:*
`{ref_link}`

📊 *Ваша статистика:*
├ Рефералов: {len(referrals)} {plural_form(len(referrals), ['человек', 'человека', 'человек'])}
└ Заработано: {format_currency(total_earned)}"""

    keyboard = [[InlineKeyboardButton("« Назад", callback_data='main_menu')]]
    await query.edit_message_text(
        text=ref_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_referral_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка реферального бонуса"""
    user = context.user_data.get('user')
    referrer_id = context.user_data.get('referrer_id')
    
    if not user or not referrer_id:
        return
    
    # Проверяем, что пользователь ещё не был зарегистрирован как реферал
    if not db.check_referral_exists(referrer_id, user.id):
        # Создаем запись о реферале
        db.create_referral(referrer_id, user.id)
        
        # Начисляем бонус рефереру
        referrer = db.get_user(referrer_id)
        if referrer:
            referrer.balance += REFERRAL_BONUS
            db.session.commit()
            
            # Отправляем уведомление рефереру
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 Поздравляем! По вашей ссылке зарегистрировался новый пользователь.\n"
                         f"💰 Вам начислен бонус: {format_currency(REFERRAL_BONUS)}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logging.error(f"Error sending referral bonus notification: {e}")
