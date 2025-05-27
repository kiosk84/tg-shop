from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import ADMIN_IDS
from utils.database import Database
from utils.keyboards import Keyboards
from utils.helpers import format_currency

db = Database()

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать админ-панель"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    stats = db.get_user_statistics()
    admin_text = f"""👑 *АДМИН-ПАНЕЛЬ*

📊 *Статистика пользователей:*
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
        await update.callback_query.edit_message_text(
            text=admin_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            text=admin_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команд админ-панели"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Недостаточно прав", show_alert=True)
        return
    
    command = query.data
    
    if command == 'admin_stats':
        stats = db.get_global_statistics()
        stats_text = f"""📊 *СТАТИСТИКА БОТА*

👥 Всего пользователей: *{stats['total_users']}*
💰 Общий баланс: *{format_currency(stats['total_balance'])}*
📈 Всего заработано: *{format_currency(stats['total_earned'])}*
💸 Всего выведено: *{format_currency(stats['total_withdrawals'])}*
📈 Всего инвестировано: *{format_currency(stats['total_investments'])}*
💎 Общая прибыль: *{format_currency(stats['total_profit'])}*

📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"""

        keyboard = Keyboards.back_to_admin()
        await query.edit_message_text(
            text=stats_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

    elif command == 'admin_broadcast':
        context.user_data['waiting_for'] = 'broadcast_message'
        await query.edit_message_text(
            text="📢 *Рассылка сообщения*\n\nВведите текст для рассылки:",
            reply_markup=Keyboards.cancel_action('admin_panel'),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif command == 'admin_block':
        context.user_data['waiting_for'] = 'user_id_to_block'
        await query.edit_message_text(
            text="🚫 *Блокировка пользователя*\n\nВведите ID пользователя:",
            reply_markup=Keyboards.cancel_action('admin_panel'),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif command == 'admin_unblock':
        context.user_data['waiting_for'] = 'user_id_to_unblock'
        await query.edit_message_text(
            text="✅ *Разблокировка пользователя*\n\nВведите ID пользователя:",
            reply_markup=Keyboards.cancel_action('admin_panel'),
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений для админ-команд"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    waiting_for = context.user_data.get('waiting_for')
    if not waiting_for:
        return

    message = update.message.text
    
    if waiting_for == 'broadcast_message':
        # Отправка сообщения всем пользователям
        success = 0
        failed = 0
        users = db.get_all_users()
        
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user.user_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN
                )
                success += 1
            except Exception:
                failed += 1

        stats = db.get_user_statistics()
        result = f"""📢 *Результаты рассылки*

✅ Успешно отправлено: *{success}*
❌ Ошибок отправки: *{failed}*
📊 Всего пользователей: *{stats['total_users']}*"""

        await update.message.reply_text(
            text=result,
            reply_markup=Keyboards.back_to_admin(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif waiting_for in ['user_id_to_block', 'user_id_to_unblock']:
        try:
            target_id = int(message)
            user = db.get_user(target_id)
            
            if not user:
                await update.message.reply_text(
                    "❌ Пользователь не найден",
                    reply_markup=Keyboards.back_to_admin()
                )
                return

            if waiting_for == 'user_id_to_block':
                db.block_user(target_id)
                action = "заблокирован"
            else:
                db.unblock_user(target_id)
                action = "разблокирован"

            await update.message.reply_text(
                f"✅ Пользователь {target_id} успешно {action}",
                reply_markup=Keyboards.back_to_admin()
            )
        
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат ID",
                reply_markup=Keyboards.back_to_admin()
            )
    
    del context.user_data['waiting_for']