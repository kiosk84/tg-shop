from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import ADMIN_IDS
from utils.keyboards import Keyboards
from utils.database import Database
from utils.helpers import format_currency

db = Database()

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать админ-панель"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    keyboard = Keyboards.admin_panel()
    admin_text = """👑 *АДМИН ПАНЕЛЬ*

🎛️ Доступные функции:

👥 Управление пользователями:
• Просмотр статистики
• Блокировка/разблокировка
• Поиск пользователей

📢 Рассылка:
• Отправка сообщений всем
• Отправка конкретному пользователю

📊 Аналитика:
• Общая статистика бота
• Топ пользователей
• Финансовая статистика"""

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=admin_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            text=admin_text,
            reply_markup=keyboard,
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

        result = f"""📢 *Результаты рассылки*

✅ Успешно отправлено: *{success}*
❌ Ошибок отправки: *{failed}*
📊 Всего пользователей: *{len(users)}*"""

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