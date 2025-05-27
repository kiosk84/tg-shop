from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.database import Database
from models.user import User, Investment
from datetime import datetime, timedelta
from decimal import Decimal

# Конфигурация инвестиционных планов
INVESTMENT_PLANS = {
    'basic': {
        'name': 'Базовый',
        'min_amount': 100,
        'daily_profit': 0.01,
        'emoji': '🥉'
    },
    'advanced': {
        'name': 'Продвинутый',
        'min_amount': 500,
        'daily_profit': 0.015,
        'emoji': '🥈'
    },
    'vip': {
        'name': 'VIP',
        'min_amount': 1000,
        'daily_profit': 0.02,
        'emoji': '🥇'
    }
}

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
        db = Database()
        user = db.get_user(update.effective_user.id)
        
        if user:
            # Подсчитываем активные инвестиции
            active_investments = [inv for inv in user.investments if not inv.is_finished]
            total_profit = sum(inv.current_profit for inv in user.investments)
            
            stats_text = f"""📊 *Ваши инвестиции*:

💰 Всего инвестировано: {user.total_invested}₽
📈 Общий доход: {total_profit}₽
🔄 Активные инвестиции: {len(active_investments)}"""

            # Если есть активные инвестиции, показываем их
            if active_investments:
                stats_text += "\n\n*Активные инвестиции:*"
                for inv in active_investments:
                    plan = INVESTMENT_PLANS[inv.plan_type]
                    stats_text += f"\n{plan['emoji']} {plan['name']}: {inv.amount}₽ (+{inv.current_profit}₽)"
        else:
            stats_text = "У вас пока нет инвестиций"

        keyboard = [[InlineKeyboardButton("« Назад", callback_data='investments')]]
        await query.edit_message_text(
            text=stats_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return

    # Обработка выбора плана
    if query.data.startswith('invest_'):
        plan_type = query.data.replace('invest_', '')
        if plan_type in INVESTMENT_PLANS:
            plan = INVESTMENT_PLANS[plan_type]
            keyboard = [
                [InlineKeyboardButton(f"💰 Инвестировать {plan['min_amount']}₽", 
                                    callback_data=f'confirm_invest_{plan_type}_{plan["min_amount"]}')],
                [InlineKeyboardButton("« Назад", callback_data='investments')]
            ]
            
            text = f"""{plan['emoji']} *{plan['name']} план*

💵 Минимальная сумма: {plan['min_amount']}₽
📈 Ежедневный доход: {plan['daily_profit'] * 100}%
⏱ Срок: 30 дней

Хотите инвестировать {plan['min_amount']}₽?"""
            
            await query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return

    # Подтверждение инвестиции
    if query.data.startswith('confirm_invest_'):
        _, plan_type, amount = query.data.split('_')[1:]
        amount = int(amount)
        
        db = Database()
        user = db.get_user(update.effective_user.id)
        
        if not user:
            await query.edit_message_text(
                text="❌ Ошибка: пользователь не найден",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("« Назад", callback_data='investments')
                ]])
            )
            return
        
        if user.balance < amount:
            await query.edit_message_text(
                text=f"❌ Недостаточно средств\nНеобходимо: {amount}₽\nДоступно: {user.balance}₽",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("« Назад", callback_data='investments')
                ]])
            )
            return
        
        # Создаем инвестицию
        investment = Investment(
            user_id=user.id,
            plan_type=plan_type,
            amount=amount,
            daily_profit=INVESTMENT_PLANS[plan_type]['daily_profit'],
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=30)
        )
        
        # Обновляем баланс пользователя
        user.balance -= amount
        user.total_invested += amount
        user.investments.append(investment)
        
        db.session.add(investment)
        db.session.commit()
        
        await query.edit_message_text(
            text=f"""✅ *Инвестиция создана успешно!*

💰 Сумма: {amount}₽
📈 План: {INVESTMENT_PLANS[plan_type]['name']}
💵 Ежедневный доход: {INVESTMENT_PLANS[plan_type]['daily_profit'] * 100}%
📅 Срок: 30 дней""",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« К списку инвестиций", callback_data='investments')
            ]]),
            parse_mode='Markdown'
        )
        return

    # Если не обработано выше, показываем заглушку
    await query.edit_message_text(
        text="🚧 Этот функционал находится в разработке",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("« Назад", callback_data='investments')
        ]])
    )