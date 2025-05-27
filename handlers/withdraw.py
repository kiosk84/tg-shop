from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import MIN_WITHDRAW, ADMIN_IDS
from utils.keyboards import Keyboards
from utils.database import Database
from utils.helpers import format_currency, validate_amount, validate_payment_details

db = Database()

async def handle_withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int = None):
    """Обработка запроса на вывод средств"""
    query = update.callback_query
    user_id = query.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await query.edit_message_text(
            text="❌ Пользователь не найден. Пожалуйста, начните с /start.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Если указана сумма, обрабатываем запрос на вывод
    if amount:
        if amount > user.balance:
            await query.answer("❌ Недостаточно средств", show_alert=True)
            return
        
        # Показываем меню выбора способа оплаты
        keyboard = Keyboards.payment_methods(amount)
        await query.edit_message_text(
            text=f"""💳 *Вывод {amount}₽*

Выберите способ вывода средств:""",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Показываем меню вывода средств
    keyboard = Keyboards.withdrawal_menu(user.balance)
    
    if user.balance < MIN_WITHDRAW:
        needed = MIN_WITHDRAW - user.balance
        withdraw_text = f"""❌ *Недостаточно средств*

💰 Ваш баланс: *{format_currency(user.balance)}*
💳 Минимум для вывода: *{format_currency(MIN_WITHDRAW)}*
📉 Не хватает: *{format_currency(needed)}*

🚀 *Как быстро заработать:*
• 👥 Приглашайте друзей
• 🎁 Получайте ежедневный бонус
• 📈 Инвестируйте средства"""
    else:
        withdraw_text = f"""💸 *Вывод средств*

📊 *Ваша статистика:*
💰 Баланс: *{format_currency(user.balance)}*
📈 Всего заработано: *{format_currency(user.total_earned)}*
💸 Выведено: *{format_currency(user.withdrawals)}*

ℹ️ Минимальная сумма: *{format_currency(MIN_WITHDRAW)}*
⚡️ Срок обработки: до 24 часов
🔒 Транзакции защищены

💡 Выберите сумму для вывода:"""

    await query.edit_message_text(
        text=withdraw_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

async def process_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка суммы и способа вывода"""
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith('confirm_withdraw_'):
        # Обработка подтверждения суммы
        amount = float(data.split('_')[-1])
        user = db.get_user(user_id)
        
        if amount > user.balance:
            await query.answer("❌ Недостаточно средств", show_alert=True)
            return
        
        if amount < MIN_WITHDRAW:
            await query.answer(f"❌ Минимальная сумма {format_currency(MIN_WITHDRAW)}", show_alert=True)
            return
        
        # Показываем методы оплаты
        keyboard = Keyboards.payment_methods(amount)
        payment_text = f"""💳 *Выберите способ вывода*

💰 Сумма: *{format_currency(amount)}*
⚡ Время обработки: до 24 часов
🔒 Транзакция защищена"""

        await query.edit_message_text(
            text=payment_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        
    elif data.startswith('payment_'):
        # Обработка выбора метода оплаты
        _, method, amount = data.split('_')
        amount = float(amount)
        
        context.user_data['withdraw'] = {
            'amount': amount,
            'method': method,
            'user_id': user_id
        }
        
        # Запрашиваем реквизиты
        method_names = {
            'card': 'Банковской карты',
            'qiwi': 'QIWI кошелька',
            'ymoney': 'ЮMoney'
        }
        
        await query.edit_message_text(
            f"""💳 *Ввод реквизитов для {method_names[method]}*

💰 Сумма: *{format_currency(amount)}*
💳 Система: *{method.upper()}*

✏️ Отправьте реквизиты для вывода средств:""",
            reply_markup=Keyboards.cancel_action('withdraw'),
            parse_mode=ParseMode.MARKDOWN
        )
        
        context.user_data['waiting_for'] = 'payment_details'

async def handle_payment_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка введенных реквизитов"""
    if 'withdraw' not in context.user_data:
        await update.message.reply_text(
            "❌ Что-то пошло не так. Начните вывод заново.",
            reply_markup=Keyboards.back_to_menu()
        )
        return
    
    withdraw_data = context.user_data['withdraw']
    details = update.message.text.strip()
    
    # Проверяем реквизиты
    is_valid, error_msg = validate_payment_details(withdraw_data['method'], details)
    if not is_valid:
        await update.message.reply_text(
            error_msg,
            reply_markup=Keyboards.cancel_action('withdraw')
        )
        return
    
    # Создаем заявку на вывод
    user_id = withdraw_data['user_id']
    amount = withdraw_data['amount']
    method = withdraw_data['method']
    
    withdrawal = db.create_withdrawal_request(user_id, amount, method, details)
    if not withdrawal:
        await update.message.reply_text(
            "❌ Ошибка создания заявки. Попробуйте позже.",
            reply_markup=Keyboards.back_to_menu()
        )
        return
    
    # Уведомляем пользователя
    success_text = f"""✅ *Заявка на вывод создана!*

💰 Сумма: *{format_currency(amount)}*
💳 Система: *{method.upper()}*
🆔 Номер заявки: `{withdrawal.id}`

⏳ Срок обработки до 24 часов
📱 Статус можно проверить в разделе "История выводов"
⚡️ Администраторы уведомлены о вашей заявке"""

    await update.message.reply_text(
        success_text,
        reply_markup=Keyboards.withdrawal_history(),
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Уведомляем админов
    await notify_admins_withdrawal(context, user_id, withdrawal)
    
    # Очищаем данные
    del context.user_data['withdraw']
    del context.user_data['waiting_for']

async def notify_admins_withdrawal(context: ContextTypes.DEFAULT_TYPE, user_id: int, withdrawal):
    """Уведомление администраторов о новой заявке"""
    admin_text = f"""💰 *НОВАЯ ЗАЯВКА НА ВЫВОД*

👤 Пользователь: `{user_id}`
💵 Сумма: *{format_currency(withdrawal.amount)}*
💳 Система: *{withdrawal.method.upper()}*
📝 Реквизиты: `{withdrawal.details}`
🆔 Номер заявки: `{withdrawal.id}`
📅 Дата: {withdrawal.date.strftime('%d.%m.%Y %H:%M')}"""

    keyboard = Keyboards.admin_withdrawal_actions(withdrawal.id)
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            print(f"Ошибка отправки уведомления админу {admin_id}: {e}")