import logging
import json
import os
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import TelegramError

# Загрузка конфигурации из .env файла
from dotenv import load_dotenv
load_dotenv()

# 🔐 Конфигурация бота
TOKEN = os.getenv('BOT_TOKEN')
DATA_FILE = 'users_data.json'
BLOCKED_USERS_FILE = 'blocked_users.json'
MIN_WITHDRAW = int(os.getenv('MIN_WITHDRAW', 50))
DAILY_BONUS = int(os.getenv('DAILY_BONUS', 2))
REFERRAL_BONUS = int(os.getenv('REFERRAL_BONUS', 5))

# Чат для аналитики
ANALYTICS_CHAT_ID = os.getenv('ANALYTICS_CHAT_ID')

# 👑 Админы бота
ADMIN_IDS = [int(id_) for id_ in os.getenv('ADMIN_IDS', '').split(',') if id_]

# 📢 Настройки канала
CHANNEL_ID = os.getenv('CHANNEL_ID')
CHANNEL_LINK = os.getenv('CHANNEL_LINK')
CHANNEL_NAME = os.getenv('CHANNEL_NAME')

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

# 💾 Система сохранения данных
def load_users_data():
    """Загрузка данных пользователей из файла"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except (json.JSONDecodeError, ValueError):
            logging.warning("Ошибка загрузки данных, создаем новый файл")
    return {}

def save_users_data():
    """Сохранение данных пользователей в файл"""
    try:
        data_to_save = {str(k): v for k, v in users.items()}
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logging.error(f"Ошибка сохранения данных: {e}")

def load_blocked_users():
    """Загрузка списка заблокированных пользователей"""
    if os.path.exists(BLOCKED_USERS_FILE):
        try:
            with open(BLOCKED_USERS_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except (json.JSONDecodeError, ValueError):
            logging.warning("Ошибка загрузки заблокированных пользователей")
    return set()

def save_blocked_users():
    """Сохранение списка заблокированных пользователей"""
    try:
        with open(BLOCKED_USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(blocked_users), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения заблокированных пользователей: {e}")

# 📊 Загрузка данных при запуске
users = load_users_data()
blocked_users = load_blocked_users()

# 🛡️ Проверка на админа
def is_admin(user_id: int) -> bool:
    """Проверка является ли пользователь админом"""
    return user_id in ADMIN_IDS

# 🚫 Проверка на блокировку
def is_blocked(user_id: int) -> bool:
    """Проверка заблокирован ли пользователь"""
    return user_id in blocked_users

# 🔍 Проверка подписки на канал
async def check_channel_subscription(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Проверка подписки пользователя на канал"""
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except TelegramError as e:
        logger.error(f"Ошибка проверки подписки для пользователя {user_id}: {e}")
        return False

# 🎯 Функция создания пользователя
def create_user(user_id: int, ref_id: int = None):
    """Создание нового пользователя с реферальной системой"""
    users[user_id] = {
        'balance': 0,
        'referrals': [],
        'last_bonus': datetime.min.isoformat(),
        'total_earned': 0,
        'withdrawals': 0,
        'join_date': datetime.now().isoformat(),
        'channel_joined': False
    }
    
    # Обработка реферальной ссылки
    if ref_id and ref_id != user_id and ref_id in users:
        if user_id not in users[ref_id]['referrals']:
            users[ref_id]['balance'] += REFERRAL_BONUS
            users[ref_id]['total_earned'] += REFERRAL_BONUS
            users[ref_id]['referrals'].append(user_id)
            logger.info(f"Пользователь {user_id} присоединился по реферальной ссылке {ref_id}")
    
    save_users_data()

# 📢 Экран проверки подписки
async def show_channel_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать экран проверки подписки на канал"""
    user_name = update.effective_user.first_name or "Друг"
    
    channel_text = f"""🔒 *Доступ ограничен*

👋 Привет, {user_name}!

📢 Для использования бота необходимо подписаться на наш канал:

🎯 *{CHANNEL_NAME}*

💎 В канале вы найдете:
• 🚀 Эксклюзивные промокоды
• 💰 Дополнительные способы заработка
• 📈 Актуальные новости проекта
• 🎁 Специальные бонусы для подписчиков

👇 Подпишитесь и нажмите "Проверить подписку"""
    
    keyboard = [
        [InlineKeyboardButton("📢 Подписаться на канал", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Проверить подписку", callback_data='check_subscription')]
    ]
    
    if update.message:
        await update.message.reply_text(
            channel_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                channel_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        except TelegramError:
            await update.callback_query.answer("Проверьте подписку на канал")

# 🔍 Обработка проверки подписки
async def handle_subscription_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка проверки подписки на канал"""
    query = update.callback_query
    user_id = query.from_user.id

    # Проверка на блокировку
    if is_blocked(user_id):
        await query.answer("❌ Вы заблокированы в боте", show_alert=True)
        return

    is_subscribed = await check_channel_subscription(context, user_id)

    if is_subscribed:
        if user_id in users:
            users[user_id]['channel_joined'] = True
            save_users_data()

        await query.answer("✅ Отлично! Подписка подтверждена!", show_alert=True)
        await start(update, context)
    else:
        await query.answer("❌ Подписка не найдена. Пожалуйста, подпишитесь на канал.", show_alert=True)
        await show_channel_check(update, context)

# 👑 Админ панель
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать админ панель"""
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
    
    keyboard = [
        [InlineKeyboardButton("📊 Статистика бота", callback_data='admin_stats'),
         InlineKeyboardButton("👥 Управление", callback_data='admin_users')],
        [InlineKeyboardButton("📢 Рассылка всем", callback_data='admin_broadcast'),
         InlineKeyboardButton("💬 Отправить пользователю", callback_data='admin_send_user')],
        [InlineKeyboardButton("🚫 Заблокировать", callback_data='admin_block'),
         InlineKeyboardButton("✅ Разблокировать", callback_data='admin_unblock')],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
    ]
    
    if update.message:
        await update.message.reply_text(
            admin_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.callback_query.edit_message_text(
            admin_text,
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
    
    # Создание пользователя если его нет
    if user_id not in users:
        ref_id = int(ref) if ref and ref.isdigit() else None
        create_user(user_id, ref_id)
    
    # Проверка подписки на канал (админы проходят без проверки)
    if not is_admin(user_id):
        is_subscribed = await check_channel_subscription(context, user_id)
        if not is_subscribed:
            await show_channel_check(update, context)
            return
    
    # Обновляем статус подписки
    users[user_id]['channel_joined'] = True
    save_users_data()
    
    # 🎨 Клавиатура для обычных пользователей
    keyboard = [
        [InlineKeyboardButton("💰 Мой баланс", callback_data='balance'),
         InlineKeyboardButton("📊 Статистика", callback_data='stats')],
        [InlineKeyboardButton("🎁 Ежедневный бонус", callback_data='bonus'),
         InlineKeyboardButton("👥 Пригласить друзей", callback_data='referral')],
        [InlineKeyboardButton("💸 Вывести средства", callback_data='withdraw'),
         InlineKeyboardButton("ℹ️ Как заработать", callback_data='info')],
        [InlineKeyboardButton("🏆 Топ пользователей", callback_data='top'),
         InlineKeyboardButton("📢 Наш канал", url=CHANNEL_LINK)]
    ]
    
    # Добавляем админ кнопку для админов
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("👑 АДМИН ПАНЕЛЬ", callback_data='admin_panel')])
    
    welcome_text = f"""🌟 *Добро пожаловать, {user_name}!*

💎 Вы в самом крутом заработок-боте Telegram!

🚀 *Что вас ждет:*
• 💰 Зарабатывайте приглашая друзей
• 🎁 Получайте ежедневные бонусы
• 💸 Выводите реальные деньги
• 🏆 Участвуйте в рейтинге

✨ Начните зарабатывать прямо сейчас!"""
    
    if update.message:
        await update.message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                welcome_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        except TelegramError:
            await update.callback_query.message.reply_text(
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
    if user_id not in users:
        await query.edit_message_text(
            "❌ Пожалуйста, начните сначала с команды /start",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Перезапустить", callback_data='menu')
            ]])
        )
        return
    
    # Админ функции
    if query.data == 'admin_panel' and is_admin(user_id):
        await show_admin_panel(update, context)
        return
    
    elif query.data == 'admin_stats' and is_admin(user_id):
        total_users = len(users)
        total_balance = sum(user.get('balance', 0) for user in users.values())
        total_earned = sum(user.get('total_earned', 0) for user in users.values())
        total_withdrawals = sum(user.get('withdrawals', 0) for user in users.values())
        blocked_count = len(blocked_users)
        
        stats_text = f"""📊 *СТАТИСТИКА БОТА*

👥 Всего пользователей: *{total_users}*
🚫 Заблокировано: *{blocked_count}*
💰 Общий баланс: *{total_balance} ₽*
📈 Всего заработано: *{total_earned} ₽*
💸 Всего выведено: *{total_withdrawals} ₽*

📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"""
        
        back_button = [[InlineKeyboardButton("⬅️ Админ панель", callback_data='admin_panel')]]
        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(back_button),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    elif query.data == 'admin_broadcast' and is_admin(user_id):
        context.user_data['waiting_for'] = 'broadcast_message'
        await query.edit_message_text(
            "📢 *РАССЫЛКА СООБЩЕНИЯ*\n\nОтправьте сообщение, которое хотите разослать всем пользователям:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data='admin_panel')
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    elif query.data == 'admin_send_user' and is_admin(user_id):
        context.user_data['waiting_for'] = 'user_id_for_message'
        await query.edit_message_text(
            "💬 *ОТПРАВКА ПОЛЬЗОВАТЕЛЮ*\n\nВведите ID пользователя:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data='admin_panel')
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    elif query.data == 'admin_block' and is_admin(user_id):
        context.user_data['waiting_for'] = 'user_id_to_block'
        await query.edit_message_text(
            "🚫 *БЛОКИРОВКА ПОЛЬЗОВАТЕЛЯ*\n\nВведите ID пользователя для блокировки:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data='admin_panel')
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    elif query.data == 'admin_unblock' and is_admin(user_id):
        context.user_data['waiting_for'] = 'user_id_to_unblock'
        await query.edit_message_text(
            "✅ *РАЗБЛОКИРОВКА ПОЛЬЗОВАТЕЛЯ*\n\nВведите ID пользователя для разблокировки:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data='admin_panel')
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Проверка подписки на канал для обычных пользователей
    if query.data != 'check_subscription' and not is_admin(user_id):
        is_subscribed = await check_channel_subscription(context, user_id)
        if not is_subscribed:
            await show_channel_check(update, context)
            return
    
    user = users[user_id]
    back_button = [[InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]]
    
    if query.data == 'balance':
        balance_text = f"""💰 *Ваш баланс*

💵 Текущий баланс: *{user['balance']} ₽*
📈 Всего заработано: *{user.get('total_earned', 0)} ₽*
💸 Выведено: *{user.get('withdrawals', 0)} ₽*
👥 Приглашено друзей: *{len(user['referrals'])}*

📅 Дата регистрации: {datetime.fromisoformat(user.get('join_date', datetime.now().isoformat())).strftime('%d.%m.%Y')}"""
        
        await query.edit_message_text(
            balance_text,
            reply_markup=InlineKeyboardMarkup(back_button),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == 'stats':
        join_date = datetime.fromisoformat(user.get('join_date', datetime.now().isoformat()))
        days_active = (datetime.now() - join_date).days + 1
        avg_daily = round(user.get('total_earned', 0) / days_active, 2) if days_active > 0 else 0
        
        stats_text = f"""📊 *Ваша статистика*

🎯 Дней в боте: *{days_active}*
📈 Средний доход в день: *{avg_daily} ₽*
👥 Активных рефералов: *{len(user['referrals'])}*
🎁 Последний бонус: {datetime.fromisoformat(user['last_bonus']).strftime('%d.%m.%Y') if user['last_bonus'] != datetime.min.isoformat() else 'Не получен'}
📢 Подписка на канал: {'✅ Активна' if user.get('channel_joined', False) else '❌ Неактивна'}

🏆 Ваш уровень: {'🥇 VIP' if user['balance'] >= 100 else '🥈 Активный' if user['balance'] >= 50 else '🥉 Новичок'}"""
        
        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(back_button),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == 'bonus':
        now = datetime.now()
        last_bonus = datetime.fromisoformat(user['last_bonus']) if user['last_bonus'] != datetime.min.isoformat() else datetime.min
        
        if now - last_bonus >= timedelta(days=1):
            user['balance'] += DAILY_BONUS
            user['total_earned'] = user.get('total_earned', 0) + DAILY_BONUS
            user['last_bonus'] = now.isoformat()
            save_users_data()
            
            bonus_text = f"""🎉 *Поздравляем!*

✨ Вы получили ежедневный бонус!
💰 +{DAILY_BONUS} ₽ добавлено на баланс

💵 Текущий баланс: *{user['balance']} ₽*

🔥 Приходите завтра за новым бонусом!"""
        else:
            remaining = timedelta(days=1) - (now - last_bonus)
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds // 60) % 60
            
            bonus_text = f"""⏰ *Ежедневный бонус*

⏳ Следующий бонус будет доступен через:
🕐 *{hours} ч. {minutes} мин.*

💡 *Совет:* Пока ждете, пригласите друзей и получите по {REFERRAL_BONUS} ₽ за каждого!"""
        
        await query.edit_message_text(
            bonus_text,
            reply_markup=InlineKeyboardMarkup(back_button),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == 'referral':
        username = context.bot.username
        ref_link = f"https://t.me/{username}?start={user_id}"
        
        referral_text = f"""👥 *Реферальная программа*

💰 Зарабатывайте *{REFERRAL_BONUS} ₽* за каждого друга!

🔗 *Ваша реферальная ссылка:*
`{ref_link}`

📊 *Ваши результаты:*
👥 Приглашено: *{len(user['referrals'])}* друзей
💰 Заработано с рефералов: *{len(user['referrals']) * REFERRAL_BONUS} ₽*

🚀 *Как это работает:*
1️⃣ Отправьте ссылку друзьям
2️⃣ Они переходят и запускают бота
3️⃣ Вы получаете {REFERRAL_BONUS} ₽ мгновенно!

💡 *Важно:* Друзья должны подписаться на канал!"""
        
        share_button = [
            [InlineKeyboardButton("📤 Поделиться ссылкой", url=f"https://t.me/share/url?url={ref_link}&text=🚀 Присоединяйся к крутому заработок-боту! Зарабатывай деньги легко и быстро!")],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
        ]
        
        await query.edit_message_text(
            referral_text,
            reply_markup=InlineKeyboardMarkup(share_button),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == 'withdraw':
        # Формируем текст со статистикой
        total_earned = user.get('total_earned', 0)
        total_withdrawn = user.get('withdrawals', 0)
        pending_withdrawals = sum(
            w['amount'] 
            for w in user.get('withdrawals_history', []) 
            if w['status'] == 'pending'
        )
        
        withdraw_buttons = []
        if user['balance'] >= MIN_WITHDRAW:
            withdraw_buttons = [
                [InlineKeyboardButton(f"💸 Вывести {MIN_WITHDRAW} ₽", callback_data=f'confirm_withdraw_{MIN_WITHDRAW}')],
                [InlineKeyboardButton(f"💰 Вывести всё ({user['balance']} ₽)", callback_data=f'confirm_withdraw_{user['balance']}')],
                [InlineKeyboardButton("📋 История выводов", callback_data='history')],
                [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
            ]
            
            withdraw_text = f"""💸 *Вывод средств*

📊 *Ваша статистика:*
💰 Баланс: *{user['balance']} ₽*
📈 Всего заработано: *{total_earned} ₽*
💸 Выведено: *{total_withdrawn} ₽*
⏳ В обработке: *{pending_withdrawals} ₽*

ℹ️ Минимальная сумма: *{MIN_WITHDRAW} ₽*
⚡️ Срок обработки: до 24 часов
🔒 Транзакции защищены

💡 Выберите сумму для вывода:"""
        else:
            needed = MIN_WITHDRAW - user['balance']
            withdraw_buttons = [
                [InlineKeyboardButton("👥 Пригласить друзей", callback_data='referral')],
                [InlineKeyboardButton("🎁 Получить бонус", callback_data='bonus')],
                [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
            ]
            withdraw_text = f"""❌ *Недостаточно средств*

💰 Ваш баланс: *{user['balance']} ₽*
💳 Минимум для вывода: *{MIN_WITHDRAW} ₽*
📉 Не хватает: *{needed} ₽*

🚀 *Как быстро заработать:*
• 👥 Пригласите {max(1, needed // REFERRAL_BONUS)} друзей (+{REFERRAL_BONUS} ₽ за каждого)
• 🎁 Получите ежедневный бонус (+{DAILY_BONUS} ₽)
• 📢 Следите за обновлениями в канале"""
        
        await query.edit_message_text(
            withdraw_text,
            reply_markup=InlineKeyboardMarkup(withdraw_buttons),
            parse_mode=ParseMode.MARKDOWN
        )
    
    # Обработка подтверждения вывода
    elif query.data.startswith('confirm_withdraw_'):
        amount = int(query.data.split('_')[-1])
        await handle_withdraw_request(update, context, amount)
    
    # Обработка выбора платежной системы
    elif query.data.startswith('payment_'):
        await handle_payment_method(update, context)
    
    # Обработка действий с заявками на вывод
    elif query.data.startswith(('approve_', 'reject_')):
        await handle_withdrawal_action(update, context)
    
    elif query.data == 'top':
        await show_top_users(update, context)
    
    elif query.data == 'info':
        await show_info(update, context)
    
    elif query.data == 'history':
        await show_withdrawal_history(update, context)
    
    elif query.data == 'menu':
        await start(update, context)

async def handle_withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int):
    """Обработка запроса на вывод средств"""
    query = update.callback_query
    user_id = query.from_user.id
    user = users[user_id]
    
    if amount < MIN_WITHDRAW:
        await query.answer(f"❌ Минимальная сумма вывода {MIN_WITHDRAW} ₽", show_alert=True)
        return
    
    if amount > user['balance']:
        await query.answer("❌ Недостаточно средств на балансе", show_alert=True)
        return
    
    payment_keyboard = [
        [InlineKeyboardButton("💳 Банковская карта", callback_data=f'payment_card_{amount}')],
        [InlineKeyboardButton("💰 QIWI", callback_data=f'payment_qiwi_{amount}')],
        [InlineKeyboardButton("📱 ЮMoney", callback_data=f'payment_ymoney_{amount}')],
        [InlineKeyboardButton("❌ Отмена", callback_data='withdraw')]
    ]
    
    payment_text = f"""💳 *Выберите способ вывода*

💰 Сумма: *{amount} ₽*
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
    user = users[user_id]
    
    # Получаем метод и сумму из callback_data
    _, method, amount = query.data.split('_')
    amount = int(amount)
    
    # Проверяем баланс еще раз
    if amount > user['balance']:
        await query.answer("❌ Недостаточно средств на балансе", show_alert=True)
        return
    
    if amount < MIN_WITHDRAW:
        await query.answer(f"❌ Минимальная сумма вывода {MIN_WITHDRAW} ₽", show_alert=True)
        return
    
    # Сохраняем данные для следующего шага
    context.user_data['withdraw'] = {
        'amount': amount,
        'method': method,
        'user_id': user_id
    }
    
    await query.edit_message_text(
        f"""💳 *Ввод реквизитов*

💰 Сумма: *{amount} ₽*
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
    except ValueError:
        await query.answer("❌ Неверный формат данных", show_alert=True)
        return
    
    action, withdrawal_id = query.data.split('_')
    
    # Поиск заявки
    withdrawal = None
    withdrawal_user = None
    for u_id, user_data in users.items():
        if 'withdrawals_history' in user_data:
            for w in user_data['withdrawals_history']:
                if w['id'] == withdrawal_id:
                    withdrawal = w
                    withdrawal_user = u_id
                    break
            if withdrawal:
                break
    
    if not withdrawal:
        await query.answer("❌ Заявка не найдена", show_alert=True)
        return
    
    if action == 'approve':
        withdrawal['status'] = 'approved'
        await notify_withdrawal_status(context, withdrawal_user, withdrawal, True)
    else:
        withdrawal['status'] = 'rejected'
        # Возвращаем средства
        users[withdrawal_user]['balance'] += withdrawal['amount']
        await notify_withdrawal_status(context, withdrawal_user, withdrawal, False)
    
    save_users_data()
    await query.answer("✅ Статус заявки обновлен", show_alert=True)

async def notify_withdrawal_status(context: ContextTypes.DEFAULT_TYPE, user_id: int, withdrawal: dict, approved: bool):
    """Уведомление пользователя о статусе вывода"""
    if approved:
        text = f"""✅ *Заявка на вывод одобрена!*

💰 Сумма: *{withdrawal['amount']} ₽*
💳 Метод: *{withdrawal['method'].upper()}*
🆔 Номер заявки: `{withdrawal['id']}`

⚡️ Средства поступят в течение 24 часов"""
    else:
        text = f"""❌ *Заявка на вывод отклонена*

💰 Сумма: *{withdrawal['amount']} ₽*
💳 Метод: *{withdrawal['method'].upper()}*
🆔 Номер заявки: `{withdrawal['id']}`

💵 Средства возвращены на баланс
📞 По всем вопросам обращайтесь в поддержку"""

    try:
        await context.bot.send_message(
            user_id,
            text,
            parse_mode=ParseMode.MARKDOWN
        )
    except TelegramError:
        logger.error(f"Не удалось отправить уведомление пользователю {user_id}")

async def notify_admins_about_withdrawal(context: ContextTypes.DEFAULT_TYPE, user_id: int, withdrawal: dict):
    """Отправка уведомления админам о новой заявке на вывод"""
    try:
        user = await context.bot.get_chat(user_id)
        user_mention = f"[{user.first_name}](tg://user?id={user_id})"
    except:
        user_mention = f"User ID: `{user_id}`"
    
    admin_message = f"""💰 *НОВАЯ ЗАЯВКА НА ВЫВОД*

👤 Пользователь: {user_mention}
💵 Сумма: *{withdrawal['amount']} ₽*
💳 Способ: *{withdrawal['method'].upper()}*
📝 Реквизиты: `{withdrawal['details']}`
🆔 Номер заявки: `{withdrawal['id']}`
📅 Дата: {datetime.fromisoformat(withdrawal['date']).strftime('%d.%m.%Y %H:%M')}

📊 Статистика пользователя:
• Баланс: *{users[user_id]['balance']} ₽*
• Всего заработано: *{users[user_id].get('total_earned', 0)} ₽*
• Рефералов: *{len(users[user_id]['referrals'])}*"""

    keyboard = [[
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{withdrawal['id']}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{withdrawal['id']}")
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
    # Сортируем пользователей по заработку
    sorted_users = sorted(
        users.items(),
        key=lambda x: (x[1].get('total_earned', 0), x[1].get('balance', 0)),
        reverse=True
    )[:10]
    
    top_text = "🏆 *ТОП-10 ПОЛЬЗОВАТЕЛЕЙ*\n\n"
    
    for i, (user_id, user_data) in enumerate(sorted_users, 1):
        try:
            user = await context.bot.get_chat(user_id)
            name = user.first_name
        except:
            name = f"Пользователь {user_id}"
        
        earned = user_data.get('total_earned', 0)
        refs = len(user_data.get('referrals', []))
        
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        top_text += f"{medal} {name}\n"
        top_text += f"💰 Заработано: *{earned} ₽*\n"
        top_text += f"👥 Рефералов: *{refs}*\n\n"
    
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
    user = users[user_id]
    
    if 'withdrawals_history' not in user or not user['withdrawals_history']:
        history_text = """📋 *История выводов*

❌ У вас пока нет заявок на вывод средств"""
    else:
        history_text = "📋 *История выводов*\n\n"
        for w in reversed(user['withdrawals_history']):
            status = {
                'pending': '⏳ В обработке',
                'approved': '✅ Одобрен',
                'rejected': '❌ Отклонен'
            }.get(w['status'], '❓ Неизвестно')
            
            history_text += f"""🆔 Заявка `{w['id']}`
💰 Сумма: *{w['amount']} ₽*
💳 Метод: *{w['method'].upper()}*
📅 Дата: {datetime.fromisoformat(w['date']).strftime('%d.%m.%Y %H:%M')}
📌 Статус: {status}\n\n"""
    
    keyboard = [
        [InlineKeyboardButton("💸 Создать вывод", callback_data='withdraw')],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
    ]
    
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
    
    if 'waiting_for' not in context.user_data:
        return
    
    waiting_for = context.user_data['waiting_for']
    
    if waiting_for == 'payment_details':
        if 'withdraw' not in context.user_data:
            await update.message.reply_text("❌ Что-то пошло не так. Начните вывод заново.")
            return
            
        withdraw_data = context.user_data['withdraw']
        details = update.message.text
        
        # Проверяем минимальную сумму
        if withdraw_data['amount'] < MIN_WITHDRAW:
            await update.message.reply_text(f"❌ Минимальная сумма вывода {MIN_WITHDRAW} ₽")
            return
            
        # Проверяем баланс
        if withdraw_data['amount'] > users[user_id]['balance']:
            await update.message.reply_text("❌ Недостаточно средств на балансе")
            return
            
        # Создаем заявку на вывод
        withdrawal = {
            'id': f"W{int(datetime.now().timestamp())}",
            'amount': withdraw_data['amount'],
            'method': withdraw_data['method'],
            'details': details,
            'status': 'pending',
            'date': datetime.now().isoformat()
        }
        
        # Сохраняем заявку
        if 'withdrawals_history' not in users[user_id]:
            users[user_id]['withdrawals_history'] = []
        users[user_id]['withdrawals_history'].append(withdrawal)
        
        # Уменьшаем баланс
        users[user_id]['balance'] -= withdrawal['amount']
        users[user_id]['withdrawals'] = users[user_id].get('withdrawals', 0) + withdrawal['amount']
        save_users_data()
        
        # Уведомляем пользователя
        success_text = f"""✅ *Заявка на вывод создана!*

💰 Сумма: *{withdrawal['amount']} ₽*
💳 Система: *{withdrawal['method'].upper()}*
🆔 Номер заявки: `{withdrawal['id']}`

⏳ Срок обработки до 24 часов
📱 Статус можно проверить в разделе "История выводов"
⚡️ Администраторы уведомлены о вашей заявке"""

        keyboard = [
            [InlineKeyboardButton("📋 История выводов", callback_data='history')],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
        ]
        
        # Уведомляем админов
        await notify_admins_about_withdrawal(context, user_id, withdrawal)
        await update.message.reply_text(
            success_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Уведомляем админов
        admin_notify = f"""💰 *Новая заявка на вывод!*

👤 Пользователь: `{user_id}`
💵 Сумма: *{withdrawal['amount']} ₽*
💳 Система: *{withdrawal['method'].upper()}*
📝 Реквизиты: `{details}`
🆔 Номер заявки: `{withdrawal['id']}`"""

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    admin_notify,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Подтвердить", callback_data=f'approve_{withdrawal["id"]}'),
                        InlineKeyboardButton("❌ Отклонить", callback_data=f'reject_{withdrawal["id"]}')
                    ]])
                )
            except TelegramError:
                logger.error(f"Не удалось отправить уведомление админу {admin_id}")
        
        # Очищаем данные
        del context.user_data['withdraw']
        del context.user_data['waiting_for']
    
    elif waiting_for == 'broadcast_message' and is_admin(user_id):
        message = update.message.text
        success_count = 0
        fail_count = 0
        
        for uid in users.keys():
            try:
                await context.bot.send_message(
                    uid,
                    message,
                    parse_mode=ParseMode.MARKDOWN
                )
                success_count += 1
                await asyncio.sleep(0.1)  # Защита от флуда
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения пользователю {uid}: {e}")
                fail_count += 1
        
        result_text = f"""📢 *Рассылка завершена*

✅ Успешно отправлено: *{success_count}*
❌ Ошибок отправки: *{fail_count}*
📊 Всего пользователей: *{len(users)}*"""
        
        keyboard = [[InlineKeyboardButton("⬅️ Админ панель", callback_data='admin_panel')]]
        await update.message.reply_text(
            result_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        
        del context.user_data['waiting_for']

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Произошла ошибка при обработке обновления {update}: {context.error}")
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Произошла ошибка при обработке запроса. Пожалуйста, попробуйте позже.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Перезапустить", callback_data='menu')
                ]])
            )
    except:
        logger.exception("Ошибка при отправке сообщения об ошибке")

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
👥 Всего пользователей: {len(users)}
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

async def main():
    """Запуск бота"""
    # Создание приложения
    application = Application.builder().token(TOKEN).build()

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("chatid", get_chat_id))
    application.add_handler(CallbackQueryHandler(handle_subscription_check, pattern="^check_subscription$"))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    # Настройка периодической отправки аналитики
    job_queue = application.job_queue
    job_queue.run_repeating(send_analytics, interval=300, first=10)

    # Отправка сообщения о запуске бота
    async def send_startup_message(context: ContextTypes.DEFAULT_TYPE):
        if ANALYTICS_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=ANALYTICS_CHAT_ID,
                    text="🚀 *Бот успешно запущен!*\n\n📅 Время запуска: " + datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
                    parse_mode=ParseMode.MARKDOWN
                )
                logger.info("Отправлено сообщение о запуске бота")
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения о запуске: {e}")
    
    # Запускаем отправку сообщения о старте
    application.job_queue.run_once(send_startup_message, when=1)

    # Определяем режим запуска (webhook для Render.com, polling для локальной разработки)
    if os.getenv('RENDER'):
        # Запуск в режиме webhook на Render.com
        port = int(os.getenv('PORT', 3000))
        webhook_url = f"{os.getenv('RENDER_EXTERNAL_URL')}/{TOKEN}"
        
        # Принудительно устанавливаем webhook
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook установлен на URL: {webhook_url}")
        
        # Запускаем приложение в режиме webhook
        application.run_webhook(
            listen='0.0.0.0',
            port=port,
            url_path=TOKEN,
            webhook_url=webhook_url,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True
        )
    else:
        # Локальный запуск в режиме polling
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
            pool_timeout=30
        )

if __name__ == '__main__':
    try:
        import asyncio
        if not asyncio.get_event_loop().is_running():
            asyncio.run(main())
        else:
            asyncio.get_event_loop().create_task(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем!")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)