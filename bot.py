import logging
import os
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import TelegramError

# Импортируем настройки и утилиты
from config.settings import *
from utils.database import Database
from utils.cron_server import CronServer
from utils.helpers import format_currency
from models.user import User, WithdrawalRequest, Investment

# Импортируем обработчики
from handlers.user import check_channel_subscription, show_channel_check
from handlers.admin import handle_admin_command, handle_admin_message
from handlers.withdraw import (
    handle_withdraw_request, 
    notify_admins_withdrawal, 
    handle_payment_details
)
from handlers.investments import show_investments, handle_investment_request
from handlers.referral import show_referral_program, handle_referral_bonus

# 🎨 Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 💾 Инициализация базы данных и вспомогательных компонентов
db = Database()

async def handle_daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ежедневного бонуса"""
    user_id = update.callback_query.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await update.callback_query.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Проверяем, прошли ли 24 часа с последнего бонуса
    now = datetime.now()
    if now - user.last_bonus < timedelta(days=1):
        next_bonus = user.last_bonus + timedelta(days=1)
        hours = int((next_bonus - now).total_seconds() / 3600)
        minutes = int(((next_bonus - now).total_seconds() % 3600) / 60)
        
        await update.callback_query.answer(
            f"⏳ Следующий бонус будет доступен через {hours} ч. {minutes} мин.",
            show_alert=True
        )
        return
    
    # Начисляем бонус
    user.balance += DAILY_BONUS
    user.total_earned += DAILY_BONUS
    user.last_bonus = now
    db.session.commit()
    
    bonus_text = f"""🎁 *Ежедневный бонус получен!*

💰 Сумма: *{format_currency(DAILY_BONUS)}*
💵 Баланс: *{format_currency(user.balance)}*

⏰ Следующий бонус будет доступен через 24 часа"""

    keyboard = [[InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]]
    
    await update.callback_query.edit_message_text(
        bonus_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

# 🛡️ Проверка на админа
def is_admin(user_id: int) -> bool:
    """Проверка является ли пользователь админом"""
    return user_id in ADMIN_IDS

# 🚫 Проверка на блокировку
def is_blocked(user_id: int) -> bool:
    """Проверка заблокирован ли пользователь"""
    user = db.get_user(user_id)
    return user and user.is_blocked if user else False

# 🎯 Функция создания пользователя
async def create_user(user_id: int, ref_id: int = None):
    """Создание нового пользователя с реферальной системой"""
    # Создаем нового пользователя
    user = db.create_user(user_id)
    
    # Обработка реферальной ссылки
    if ref_id and ref_id != user_id:
        referrer = db.get_user(ref_id)
        if referrer and not db.get_referral(ref_id, user_id):
            # Создаем реферальную связь
            db.create_referral(ref_id, user_id)
            # Начисляем бонус рефереру
            referrer.balance += REFERRAL_BONUS
            referrer.total_earned += REFERRAL_BONUS
            db.session.commit()
            logger.info(f"Пользователь {user_id} присоединился по реферальной ссылке {ref_id}")
    
    return user

# 👑 Админ панель
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать админ панель"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ Недостаточно прав", show_alert=True)
        return

    stats = db.get_user_statistics()
    stats_text = f"""👑 *АДМИН-ПАНЕЛЬ*

📊 *Статистика:*
👥 Всего пользователей: *{stats['total_users']}*
✅ Активных: *{stats['active_users']}*
🚫 Заблокировано: *{stats['blocked_users']}*

📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"""

    keyboard = [
        [InlineKeyboardButton("📊 Подробная статистика", callback_data='admin_stats'),
         InlineKeyboardButton("📢 Рассылка", callback_data='admin_broadcast')],
        [InlineKeyboardButton("✉️ Написать пользователю", callback_data='admin_send_user'),
         InlineKeyboardButton("🚫 Заблокировать", callback_data='admin_block')],
        [InlineKeyboardButton("✅ Разблокировать", callback_data='admin_unblock')],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
    ]

    if update.callback_query:
        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

# 🚀 Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Временная функция для получения ID чата
    if update.message and update.message.chat.type in ['group', 'supergroup']:
        await update.message.reply_text(f"ID этого чата: `{update.message.chat.id}`", parse_mode=ParseMode.MARKDOWN)
        return

    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Друг"
    ref = context.args[0] if context.args else None
    
    # Проверка на блокировку
    if is_blocked(user_id):
        blocked_text = """🚫 *Доступ заблокирован*

❌ Ваш аккаунт заблокирован администрацией.

📞 Для разблокировки обратитесь в поддержку."""
        
        if update.message:
            await update.message.reply_text(blocked_text, parse_mode=ParseMode.MARKDOWN)
        return
    
    # Получаем или создаем пользователя
    user = db.get_user(user_id)
    if not user:
        ref_id = int(ref) if ref and ref.isdigit() else None
        user = await create_user(user_id, ref_id)
    
    # Проверка подписки на канал (админы проходят без проверки)
    if not is_admin(user_id):
        is_subscribed = await check_channel_subscription(context, user_id)
        if not is_subscribed:
            await show_channel_check(update, context)
            return
    
    # Обновляем статус подписки
    if not user.channel_joined:
        user.channel_joined = True
        db.session.commit()
    
    # 🎨 Клавиатура для обычных пользователей
    keyboard = [
        [InlineKeyboardButton("💰 Баланс", callback_data='balance'),
         InlineKeyboardButton("📊 Статистика", callback_data='stats')],
        [InlineKeyboardButton("💸 Вывод", callback_data='withdraw'),
         InlineKeyboardButton("🎁 Бонус", callback_data='bonus')],
        [InlineKeyboardButton("📈 Инвестиции", callback_data='investments'),
         InlineKeyboardButton("👥 Рефералы", callback_data='referral')],
        [InlineKeyboardButton("🏆 Топ", callback_data='top'),
         InlineKeyboardButton("ℹ️ Информация", callback_data='info')]
    ]
    
    # Добавляем кнопку админ-панели для администраторов
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data='admin_panel')])
    
    welcome_text = f"""👋 Привет, {user_name}!

💰 Баланс: *{format_currency(user.balance)}*
📈 Всего заработано: *{format_currency(user.total_earned)}*
💸 Выведено: *{format_currency(user.withdrawals)}*
👥 Рефералов: *{len(user.referrals)}*

🔥 Выберите действие:"""

    if update.callback_query:
        await update.callback_query.edit_message_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

# 🎯 Обработка кнопок
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    # Проверка на блокировку
    if is_blocked(user_id):
        await query.answer("❌ Вы заблокированы в боте", show_alert=True)
        return
    
    # Проверка существования пользователя
    user = db.get_user(user_id)
    if not user:
        await query.edit_message_text(
            "❌ Пожалуйста, начните сначала с команды /start",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Перезапустить", callback_data='menu')
            ]])
        )
        return
    
    # Проверка подписки на канал для обычных пользователей
    if query.data != 'check_subscription' and not is_admin(user_id):
        is_subscribed = await check_channel_subscription(context, user_id)
        if not is_subscribed:
            await show_channel_check(update, context)
            return
    
    # Админ функции
    if query.data == 'admin_panel' and is_admin(user_id):
        await show_admin_panel(update, context)
        return
    
    elif query.data == 'admin_stats' and is_admin(user_id):
        stats = db.get_user_statistics()
        invest_stats = db.get_investments_statistics()
        
        stats_text = f"""📊 *ПОДРОБНАЯ СТАТИСТИКА*

👥 *Пользователи:*
• Всего: *{stats['total_users']}*
• Активных: *{stats['active_users']}*
• Заблокировано: *{stats['blocked_users']}*

💰 *Финансы:*
• Инвестировано: *{format_currency(invest_stats['total_investments'])}*
• Выплачено: *{format_currency(invest_stats['total_profit_paid'])}*
• Активных планов: *{invest_stats['active_investments']}*

📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"""

        keyboard = [[InlineKeyboardButton("⬅️ Админ панель", callback_data='admin_panel')]]
        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Пользовательские функции
    elif query.data == 'stats':
        ref_count = len(user.referrals)
        ref_earnings = sum(r.bonus_paid for r in user.referrals)
        invest_earnings = sum(i.total_profit for i in user.investments)
        
        stats_text = f"""📊 *Ваша статистика*

💰 *Баланс и доход:*
• Текущий баланс: *{format_currency(user.balance)}*
• Всего заработано: *{format_currency(user.total_earned)}*
• Выведено: *{format_currency(user.withdrawals)}*

👥 *Рефералы:*
• Всего рефералов: *{ref_count}*
• Заработок с рефералов: *{format_currency(ref_earnings)}*

📈 *Инвестиции:*
• Активных планов: *{len(user.investments)}*
• Прибыль с инвестиций: *{format_currency(invest_earnings)}*

📅 Дата регистрации: {user.join_date.strftime('%d.%m.%Y %H:%M')}"""

        keyboard = [[InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]]
        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Обработка других команд
    elif query.data == 'withdraw':
        await handle_withdraw_request(update, context)
    elif query.data == 'bonus':
        await handle_daily_bonus(update, context)
    elif query.data == 'investments':
        await show_investments(update, context)
    elif query.data == 'referral':
        await show_referral_program(update, context)
    elif query.data == 'top':
        await show_top_users(update, context)
    elif query.data == 'info':
        await show_info(update, context)
    elif query.data == 'history':
        await show_withdrawal_history(update, context)
    elif query.data == 'menu':
        await start(update, context)
    # Более специфичные обработчики могут быть добавлены здесь

async def handle_withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int):
    """Обработка запроса на вывод средств"""
    query = update.callback_query
    user_id = query.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await query.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    if amount < MIN_WITHDRAW:
        await query.answer(f"❌ Минимальная сумма вывода {MIN_WITHDRAW} ₽", show_alert=True)
        return
    
    if amount > user.balance:
        await query.answer("❌ Недостаточно средств на балансе", show_alert=True)
        return
    
    payment_keyboard = [
        [InlineKeyboardButton("💳 Банковская карта", callback_data=f'payment_card_{amount}')],
        [InlineKeyboardButton("💰 QIWI", callback_data=f'payment_qiwi_{amount}')],
        [InlineKeyboardButton("📱 ЮMoney", callback_data=f'payment_ymoney_{amount}')],
        [InlineKeyboardButton("❌ Отмена", callback_data='withdraw')]
    ]
    
    payment_text = f"""💳 *Выберите способ вывода*

💰 Сумма: *{format_currency(amount)}*
⚡ Время обработки: до 24 часов
🔒 Транзакция защищена"""

    await query.edit_message_text(
        payment_text,
        reply_markup=InlineKeyboardMarkup(payment_keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора платежной системы"""
    query = update.callback_query
    user_id = query.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await query.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Получаем метод и сумму из callback_data
    _, method, amount = query.data.split('_')
    amount = int(amount)
    
    # Проверяем баланс еще раз
    if amount > user.balance:
        await query.answer("❌ Недостаточно средств на балансе", show_alert=True)
        return
    
    if amount < MIN_WITHDRAW:
        await query.answer(f"❌ Минимальная сумма вывода {format_currency(MIN_WITHDRAW)}", show_alert=True)
        return
    
    # Сохраняем данные для следующего шага
    context.user_data['withdraw'] = {
        'amount': amount,
        'method': method,
        'user_id': user_id
    }
    
    await query.edit_message_text(
        f"""💳 *Ввод реквизитов*

💰 Сумма: *{format_currency(amount)}*
💳 Система: *{method.upper()}*

✏️ Отправьте реквизиты для вывода средств:""",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data='withdraw')
        ]]),
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Устанавливаем состояние ожидания реквизитов
    context.user_data['waiting_for'] = 'payment_details'

async def handle_withdrawal_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка действий с заявками на вывод"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ Недостаточно прав", show_alert=True)
        return
        
    try:
        action, withdrawal_id = query.data.split('_')
        withdrawal_id = int(withdrawal_id)
    except (ValueError, TypeError):
        await query.answer("❌ Неверный формат данных", show_alert=True)
        return
    
    # Получаем заявку
    withdrawal = db.session.query(WithdrawalRequest).get(withdrawal_id)
    if not withdrawal:
        await query.answer("❌ Заявка не найдена", show_alert=True)
        return
    
    # Получаем пользователя
    withdrawal_user = withdrawal.user
    if not withdrawal_user:
        await query.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    if action == 'approve':
        withdrawal.status = 'approved'
        withdrawal.processed_date = datetime.now()
        withdrawal.processed_by = user_id
        withdrawal_user.withdrawals += withdrawal.amount
        db.session.commit()
        await notify_withdrawal_status(context, withdrawal_user.user_id, withdrawal, True)
    else:
        withdrawal.status = 'rejected'
        withdrawal.processed_date = datetime.now()
        withdrawal.processed_by = user_id
        # Возвращаем средства
        withdrawal_user.balance += withdrawal.amount
        db.session.commit()
        await notify_withdrawal_status(context, withdrawal_user.user_id, withdrawal, False)
    
    await query.answer("✅ Статус заявки обновлен", show_alert=True)

async def notify_withdrawal_status(context: ContextTypes.DEFAULT_TYPE, user_id: int, withdrawal: WithdrawalRequest, approved: bool):
    """Уведомление пользователя о статусе вывода"""
    if approved:
        text = f"""✅ *Заявка на вывод одобрена!*

💰 Сумма: *{format_currency(withdrawal.amount)}*
💳 Метод: *{withdrawal.method.upper()}*
🆔 Номер заявки: `{withdrawal.id}`

⚡️ Средства поступят в течение 24 часов"""
    else:
        text = f"""❌ *Заявка на вывод отклонена*

💰 Сумма: *{format_currency(withdrawal.amount)}*
💳 Метод: *{withdrawal.method.upper()}*
🆔 Номер заявки: `{withdrawal.id}`

💵 Средства возвращены на баланс
⚡️ Вы можете создать новую заявку"""

    keyboard = [
        [InlineKeyboardButton("📋 История выводов", callback_data='history')],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
    ]

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

async def notify_admins_about_withdrawal(context: ContextTypes.DEFAULT_TYPE, user_id: int, withdrawal: WithdrawalRequest):
    """Отправка уведомления админам о новой заявке на вывод"""
    try:
        user = await context.bot.get_chat(user_id)
        user_mention = f"[{user.first_name}](tg://user?id={user_id})"
    except:
        user_mention = f"User ID: `{user_id}`"
    
    db_user = db.get_user(user_id)
    if not db_user:
        logger.error(f"Пользователь {user_id} не найден в базе")
        return
    
    admin_message = f"""💰 *НОВАЯ ЗАЯВКА НА ВЫВОД*

👤 Пользователь: {user_mention}
💵 Сумма: *{format_currency(withdrawal.amount)}*
💳 Способ: *{withdrawal.method.upper()}*
📝 Реквизиты: `{withdrawal.details}`
🆔 Номер заявки: `{withdrawal.id}`
📅 Дата: {withdrawal.date.strftime('%d.%m.%Y %H:%M')}

📊 Статистика пользователя:
• Баланс: *{format_currency(db_user.balance)}*
• Всего заработано: *{format_currency(db_user.total_earned)}*
• Рефералов: *{len(db_user.referrals)}*"""

    keyboard = [[
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{withdrawal.id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{withdrawal.id}")
    ]]

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")

async def show_top_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать топ пользователей"""
    # Получаем топ-10 пользователей по заработку
    top_users = db.session.query(User)\
        .order_by(User.total_earned.desc(), User.balance.desc())\
        .limit(10)\
        .all()
    
    top_text = "🏆 *ТОП-10 ПОЛЬЗОВАТЕЛЕЙ*\n\n"
    
    for i, user in enumerate(top_users, 1):
        try:
            chat = await context.bot.get_chat(user.user_id)
            name = chat.first_name
        except:
            name = f"Пользователь {user.user_id}"
        
        refs_count = len(user.referrals)
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        
        top_text += f"{medal} {name}\n"
        top_text += f"💰 Заработано: *{format_currency(user.total_earned)}*\n"
        top_text += f"👥 Рефералов: *{refs_count}*\n\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            top_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            top_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def show_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать информацию о заработке"""
    info_text = f"""ℹ️ *Как заработать в боте*

💰 *Доступные способы заработка:*

1️⃣ *Реферальная программа*
• Приглашайте друзей по своей ссылке
• Получайте {REFERRAL_BONUS} ₽ за каждого
• Друг должен подписаться на канал

2️⃣ *Ежедневный бонус*
• Получайте {DAILY_BONUS} ₽ каждый день
• Бонус доступен раз в 24 часа
• Не пропускайте дни для максимального заработка

3️⃣ *Уровни и достижения*
• 🥉 Новичок (0-49 ₽)
• 🥈 Активный (50-99 ₽)
• 🥇 VIP (100+ ₽)

4️⃣ *Вывод средств*
• Минимальная сумма: {MIN_WITHDRAW} ₽
• Популярные платежные системы
• Быстрые выплаты до 24 часов

💡 *Советы:*
• Регулярно получайте бонусы
• Пригласите активных друзей
• Следите за обновлениями в канале"""

    keyboard = [[InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            info_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            info_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def show_withdrawal_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать историю выводов"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    withdrawals = db.session.query(WithdrawalRequest).filter_by(user_id=user.id).order_by(WithdrawalRequest.date.desc()).all()
    
    if not withdrawals:
        history_text = """📋 *История выводов*

❌ У вас пока нет заявок на вывод средств"""
    else:
        history_text = "📋 *История выводов*\n\n"
        for w in withdrawals:
            status = {
                'pending': '⏳ В обработке',
                'approved': '✅ Одобрен',
                'rejected': '❌ Отклонен'
            }.get(w.status, '❓ Неизвестно')
            
            history_text += f"""🆔 Заявка `{w.id}`
💰 Сумма: *{format_currency(w.amount)}*
💳 Система: *{w.method.upper()}*
📅 Дата: {w.date.strftime('%d.%m.%Y %H:%M')}
✨ Статус: {status}\n\n"""
    
    keyboard = [[InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            history_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            history_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    
    # Проверка на блокировку
    if is_blocked(user_id):
        await update.message.reply_text(
            "🚫 Вы заблокированы в боте",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Проверяем наличие пользователя
    user = db.get_user(user_id)
    if not user:
        await update.message.reply_text(
            "❌ Пожалуйста, начните с команды /start",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Начать", callback_data='menu')
            ]])
        )
        return
    
    # Проверка подписки для обычных пользователей
    if not is_admin(user_id):
        is_subscribed = await check_channel_subscription(context, user_id)
        if not is_subscribed:
            await show_channel_check(update, context)
            return
    
    waiting_for = context.user_data.get('waiting_for')
    
    # Обработка платежных реквизитов
    if waiting_for == 'payment_details':
        await handle_payment_details(update, context)
        return
    
    # Обработка админских команд
    elif waiting_for in ['broadcast_message', 'user_id_for_message', 'user_id_to_block', 'user_id_to_unblock'] and is_admin(user_id):
        await handle_admin_message(update, context)
        return
    
    # Для неизвестных сообщений показываем меню
    else:
        await start(update, context)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ошибок"""
    # Получаем информацию об ошибке
    error = context.error
    logger.error(f"Update {update} caused error {error}")
    
    try:
        # Получаем статистику
        stats = db.get_user_statistics()
        
        # Отправляем сообщение об ошибке админам
        error_text = f"""❌ *Произошла ошибка!*

🔄 Update ID: `{update.update_id if update else 'Unknown'}`
👤 User: `{update.effective_user.id if update and update.effective_user else 'Unknown'}`
📊 Всего пользователей: *{stats['total_users']}*
⚠️ Error: `{str(error)}`"""

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=error_text,
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                continue
                
        # Отправляем пользователю сообщение об ошибке
        if update and update.effective_message:
            error_msg = """❌ *Произошла ошибка*

Пожалуйста, попробуйте позже или обратитесь в поддержку."""
            
            await update.effective_message.reply_text(
                error_msg,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Главное меню", callback_data='menu')
                ]])
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

async def send_analytics(context: ContextTypes.DEFAULT_TYPE):
    """Отправка сообщения в чат каждые 5 минут"""
    if not ANALYTICS_CHAT_ID:
        logger.error("ANALYTICS_CHAT_ID не настроен")
        return
        
    try:
        # Проверяем существование чата перед отправкой
        chat = await context.bot.get_chat(ANALYTICS_CHAT_ID)
        if not chat:
            logger.error(f"Чат с ID {ANALYTICS_CHAT_ID} не найден")
            return
            
        now = datetime.now()
        message_text = f"""🤖 *Проверка работы бота*
📅 {now.strftime('%d.%m.%Y %H:%M')}

✅ Бот работает нормально
👥 Всего пользователей: {db.get_user_statistics()['total_users']}
🔄 Режим: {"Webhook" if os.getenv('RENDER') else "Polling"}"""
        
        try:
            await context.bot.send_message(
                chat_id=ANALYTICS_CHAT_ID,
                text=message_text,
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info("Тестовое сообщение успешно отправлено")
        except Exception as e:
            logger.error(f"Ошибка отправки аналитики: {str(e)}")
            return
    except Exception as e:
        logger.error(f"Ошибка при проверке чата: {str(e)}")
        return

    try:
        await context.bot.send_message(
            chat_id=ANALYTICS_CHAT_ID,
            text=message_text,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info("Тестовое сообщение успешно отправлено")
    except Exception as e:
        logger.error(f"Ошибка отправки аналитики: {str(e)}")
        return
        logger.error(f"Ошибка отправки сообщения: {e}")

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение ID текущего чата"""
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"ID этого чата: `{chat_id}`", parse_mode=ParseMode.MARKDOWN)

def main():
    """Запуск бота"""
    # Инициализация бота
    application = Application.builder().token(TOKEN).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    
    # Обработка нажатий на кнопки
    application.add_handler(CallbackQueryHandler(button))
    
    # Обработка текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обработка ошибок
    application.add_error_handler(error_handler)

    # Запуск бота
    if WEBHOOK_ENABLED:
        # Запуск через webhook для production
        logger.info(f"Запуск бота через webhook на порту {PORT}")
        application.run_webhook(
            listen='0.0.0.0',
            port=PORT,
            webhook_url=WEBHOOK_URL,
            url_path=TOKEN
        )
    else:
        # Запуск через polling для разработки
        logger.info("Запуск бота через long polling")
        application.run_polling()

# Запуск
if __name__ == '__main__':
    main()