from telegram import Update, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import REFERRAL_BONUS
from utils.keyboards import Keyboards
from utils.database import Database
from utils.helpers import format_currency, plural_form

db = Database()

async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
• Приглашено: *{len(referrals)}* {plural_form(len(referrals), ('друг', 'друга', 'друзей'))}
• Заработано: *{format_currency(total_earned)}*

🚀 *Как получить бонус:*
1. Отправьте другу вашу реферальную ссылку
2. Друг должен перейти по ссылке и запустить бота
3. Вы получите {format_currency(REFERRAL_BONUS)} после подписки друга на канал

💡 Чем больше друзей вы пригласите, тем больше заработаете!"""

    share_button = [[
        InlineKeyboardButton(
            "📤 Поделиться ссылкой", 
            url=f"https://t.me/share/url?url={ref_link}&text=🚀 Присоединяйся к крутому заработок-боту!"
        )
    ]]
    keyboard = Keyboards.add_back_button(share_button)

    await query.edit_message_text(
        text=ref_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

async def create_ref_link(user_id: int, bot_username: str) -> str:
    """Создание реферальной ссылки"""
    return f"https://t.me/{bot_username}?start={user_id}"