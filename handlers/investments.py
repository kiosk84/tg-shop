from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.database import get_session
from models.user import User
from sqlalchemy import select

async def show_investments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню инвестиций с доступными планами"""
    keyboard = [
        [
            InlineKeyboardButton("🥉 Базовый план", callback_data='invest_basic'),
            InlineKeyboardButton("🥈 Продвинутый план", callback_data='invest_advanced')
        ],
        [
            InlineKeyboardButton("🥇 VIP план", callback_data='invest_vip'),
            InlineKeyboardButton("📊 Мои инвестиции", callback_data='invest_stats')
        ],
        [InlineKeyboardButton("« Назад", callback_data='main_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = """💎 *Инвестиционные планы*:

🥉 *Базовый*:
├ Минимум: 100₽
└ Доход: 1% в день

🥈 *Продвинутый*:
├ Минимум: 500₽
└ Доход: 1.5% в день

🥇 *VIP*:
├ Минимум: 1000₽
└ Доход: 2% в день"""

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_investment_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор инвестиционного плана"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'invest_stats':
        async with get_session() as session:
            user = await session.scalar(
                select(User).where(User.telegram_id == update.effective_user.id)
            )
            if user:
                stats_text = f"""📊 *Ваши инвестиции*:

💰 Всего инвестировано: {user.total_invested}₽
📈 Общий доход: {user.investment_profit}₽
🔄 Активные инвестиции: {len(user.investments)}"""
            else:
                stats_text = "У вас пока нет инвестиций"

        keyboard = [[InlineKeyboardButton("« Назад", callback_data='investments')]]
        await query.edit_message_text(
            text=stats_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return

    # Заглушка для остальных действий
    await query.edit_message_text(
        text="🚧 Этот функционал находится в разработке",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("« Назад", callback_data='investments')
        ]])
    )