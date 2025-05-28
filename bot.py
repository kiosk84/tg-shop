# Файл: bot.py

import logging
import os
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List 

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import TelegramError

# --- ЗАГРУЗКА НАСТРОЕК ---
# Эта секция пытается загрузить настройки из config.settings, а если не удается,
# то из переменных окружения. Это обеспечивает гибкость.
CONFIG_LOADED_SUCCESSFULLY = False
try:
    from config.settings import * # Загружаем все из settings.py
    # Проверяем наличие ключевых переменных после импорта
    if 'TOKEN' not in locals() or not TOKEN:
        raise ImportError("TOKEN not defined or empty in config.settings.")
    # Устанавливаем значения по умолчанию, если переменные не определены в settings.py
    ADMIN_IDS = ADMIN_IDS if 'ADMIN_IDS' in locals() and isinstance(ADMIN_IDS, list) else []
    CHANNEL_ID = CHANNEL_ID if 'CHANNEL_ID' in locals() else None
    CHANNEL_LINK = CHANNEL_LINK if 'CHANNEL_LINK' in locals() else "https://t.me/your_channel_username" # Замените!
    REFERRAL_BONUS = REFERRAL_BONUS if 'REFERRAL_BONUS' in locals() else 50
    DAILY_BONUS = DAILY_BONUS if 'DAILY_BONUS' in locals() else 10
    MIN_WITHDRAW = MIN_WITHDRAW if 'MIN_WITHDRAW' in locals() else 100
    CONFIG_LOADED_SUCCESSFULLY = True
    print("INFO: Successfully loaded settings from config/settings.py")
except ImportError as e_cfg:
    print(f"WARNING: Could not import from config.settings ({e_cfg}). Falling back to environment variables.")
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(",") if admin_id.strip().isdigit()] if ADMIN_IDS_STR else []
    CHANNEL_ID = os.getenv("CHANNEL_ID") 
    CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/your_channel_username") # Замените!
    REFERRAL_BONUS = int(os.getenv("REFERRAL_BONUS", 50))
    DAILY_BONUS = int(os.getenv("DAILY_BONUS", 10))
    MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", 100))
    
    if not TOKEN:
        print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN is not defined in config.settings or environment variables. Bot cannot start.")
        exit(1)
    print("INFO: Loaded settings from environment variables.")

# --- ИМПОРТЫ УТИЛИТ, МОДЕЛЕЙ И ОБРАБОТЧИКОВ ---
# Эти импорты должны быть ниже загрузки настроек, т.к. некоторые модули могут их использовать при инициализации
try:
    from utils.database import Database
    from utils.cron_server import CronServer # Предполагаем, что он существует и корректен
    from utils.helpers import format_currency
    from models.user import User, WithdrawalRequest, Investment # Убедитесь, что модели корректны
    from handlers.user import check_channel_subscription, show_channel_check, show_balance
    from handlers.admin import handle_admin_command, handle_admin_message
    from handlers.withdraw import handle_withdraw_request, handle_payment_details
    from handlers.investments import show_investments, handle_investment_request
    from handlers.referral import show_referral_program, handle_referral_bonus
except ImportError as e_module:
    # Если TOKEN уже загружен, можно попробовать отправить уведомление админам об ошибке импорта
    # Но логгер еще не инициализирован, поэтому пока только print
    print(f"CRITICAL ERROR: Failed to import required modules: {e_module}. Please check paths and file/class existence.")
    print("This could be due to missing files, incorrect PYTHONPATH, or errors within the imported modules.")
    exit(1)

# --- КЛАССЫ БОТА ---

class BotLogger:
    @staticmethod
    def setup_logging():
        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, log_level_str, logging.INFO) # Безопасное получение уровня

        logging.basicConfig(
            format='%(asctime)s - %(name)s [%(levelname)s] - %(message)s (%(filename)s:%(lineno)d)',
            level=log_level,
            handlers=[
                logging.FileHandler('bot.log', encoding='utf-8', mode='a'), # mode='a' для дозаписи
                logging.StreamHandler()
            ]
        )
        # Уменьшаем "шум" от сторонних библиотек
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING) # Для CronServer
        logging.getLogger("telegram.ext").setLevel(logging.INFO)
        logging.getLogger("telegram.bot").setLevel(logging.INFO) 
        
        # Возвращаем главный логгер приложения
        # Этот логгер будет использоваться для общих сообщений приложения.
        # Классы внутри будут использовать свои собственные логгеры вида logging.getLogger(self.__class__.__name__)
        return logging.getLogger("TelegramBotApp")

# Инициализируем логгирование здесь, чтобы оно было доступно всем частям приложения сразу
# Глобальный логгер приложения. Классы могут использовать свой self.logger.
app_logger = BotLogger.setup_logging()


class UserService:
    def __init__(self, database: Database):
        self.db = database
        self.logger = logging.getLogger(self.__class__.__name__)

    def is_admin(self, user_id: int) -> bool:
        return user_id in ADMIN_IDS

    def is_blocked(self, user_id: int) -> bool:
        user = self.db.get_user(user_id) # get_user должен возвращать объект User или None
        return bool(user and user.is_blocked)

    async def create_user(self, user_id_tg: int, referrer_pk: Optional[int] = None) -> Optional[User]:
        # user_id_tg - Telegram ID нового пользователя
        # referrer_pk - Primary Key (User.id) реферера из таблицы users
        self.logger.debug(f"Attempting to create user with TG ID: {user_id_tg}, Referrer PK: {referrer_pk}")
        try:
            # create_user должен принимать Telegram ID и возвращать объект User или None/выбрасывать исключение
            user_obj = self.db.create_user(user_id_tg) 
            if not user_obj:
                self.logger.error(f"Failed to create user object in DB for TG ID: {user_id_tg}")
                return None

            if referrer_pk:
                # get_user_by_pk должен принимать PK и возвращать объект User
                referrer_obj = self.db.get_user_by_pk(referrer_pk) 
                
                if referrer_obj and referrer_obj.id != user_obj.id: # Убедимся, что реферер не сам пользователь
                    # get_referral должен проверять существование связи по PK
                    if not self.db.get_referral(referrer_obj.id, user_obj.id): 
                        self.db.create_referral(referrer_obj.id, user_obj.id) # Создаем связь по PK
                        referrer_obj.balance += REFERRAL_BONUS
                        referrer_obj.total_earned += REFERRAL_BONUS
                        self.db.session.commit()
                        self.logger.info(f"User {user_id_tg} (PK: {user_obj.id}) joined via referral from user {referrer_obj.user_id} (PK: {referrer_obj.id}). Bonus {REFERRAL_BONUS} awarded.")
                    else:
                        self.logger.info(f"Referral link from user PK {referrer_obj.id} to new user {user_id_tg} (PK: {user_obj.id}) already processed.")
                elif not referrer_obj:
                     self.logger.warning(f"Referrer with PK {referrer_pk} not found for new user {user_id_tg}.")
            return user_obj
        except Exception as e:
            self.logger.error(f"Exception during user creation for TG ID {user_id_tg}: {e}", exc_info=True)
            # В случае ошибки, если транзакция была начата, SQLAlchemy может потребовать rollback
            if hasattr(self.db, 'session') and self.db.session.is_active:
                 self.db.session.rollback()
            return None # Возвращаем None при ошибке

# ... (Классы BonusService, WithdrawalService, MessageBuilder, KeyboardBuilder без изменений из предыдущего ответа,
#      если они вас устраивали. Для краткости я их здесь не повторяю, но они должны быть в вашем файле)

# ПРЕДПОЛАГАЕТСЯ, ЧТО КЛАССЫ BonusService, WithdrawalService, MessageBuilder, KeyboardBuilder
# НАХОДЯТСЯ ЗДЕСЬ И ОНИ ТАКИЕ ЖЕ, КАК В ПРЕДЫДУЩЕМ ПОЛНОМ ОТВЕТЕ.
# ЕСЛИ НУЖНО, Я МОГУ ИХ СКОПИРОВАТЬ СЮДА СНОВА.
# ДЛЯ ЭТОГО ОТВЕТА Я ИХ ПРОПУСКАЮ, ЧТОБЫ СОСРЕДОТОЧИТЬСЯ НА TelegramBot И main.

# --- НАЧАЛО ПРОПУЩЕННЫХ КЛАССОВ (BonusService, WithdrawalService, MessageBuilder, KeyboardBuilder) ---
# Скопируйте их сюда из моего предыдущего полного ответа
class BonusService:
    def __init__(self, database: Database):
        self.db = database
        self.logger = logging.getLogger(self.__class__.__name__)

    def can_claim_daily_bonus(self, user: User) -> tuple[bool, Optional[timedelta]]:
        if not user.last_bonus: return True, None
        now = datetime.now()
        time_since_last = now - user.last_bonus
        if time_since_last >= timedelta(days=1): return True, None
        next_bonus_time_exact = user.last_bonus + timedelta(days=1)
        return False, next_bonus_time_exact - now

    def claim_daily_bonus(self, user: User) -> bool:
        try:
            can_claim, _ = self.can_claim_daily_bonus(user)
            if not can_claim:
                self.logger.info(f"User {user.user_id} (PK: {user.id}) - bonus claim attempt too early.")
                return False
            user.balance += DAILY_BONUS
            user.total_earned += DAILY_BONUS
            user.last_bonus = datetime.now()
            # Логика для user.bonus_streak (если есть такое поле)
            self.db.session.commit()
            self.logger.info(f"Daily bonus {DAILY_BONUS} claimed by user {user.user_id} (PK: {user.id}). New balance: {user.balance}.")
            return True
        except Exception as e:
            self.logger.error(f"Error claiming daily bonus for user {user.user_id} (PK: {user.id}): {e}", exc_info=True)
            self.db.session.rollback()
            return False

class WithdrawalService:
    def __init__(self, database: Database):
        self.db = database
        self.logger = logging.getLogger(self.__class__.__name__)

    def validate_withdrawal(self, user: User, amount: int) -> Dict[str, Any]:
        if amount < MIN_WITHDRAW: return {'valid': False, 'error': f'💰 Мин. сумма: {format_currency(MIN_WITHDRAW)}'}
        if amount > user.balance: return {'valid': False, 'error': f'💸 Недостаточно: {format_currency(amount)} (доступно: {format_currency(user.balance)})'}
        return {'valid': True, 'error': None}

    def create_withdrawal_request(self, user_obj: User, amount: int, method: str, details: str) -> Optional[WithdrawalRequest]:
        try:
            validation = self.validate_withdrawal(user_obj, amount)
            if not validation['valid']:
                self.logger.warning(f"Withdrawal validation failed for user {user_obj.user_id} (PK: {user_obj.id}): {validation.get('error')}")
                return None 
            
            withdrawal_req = WithdrawalRequest(
                user_id=user_obj.id, 
                amount=amount, method=method, details=details,
                date=datetime.now(), status='pending'
            )
            user_obj.balance -= amount
            self.db.session.add(withdrawal_req)
            self.db.session.commit()
            self.logger.info(f"Withdrawal request (ID: {withdrawal_req.id}) created for user {user_obj.user_id} (PK: {user_obj.id}), amount: {amount}.")
            return withdrawal_req
        except Exception as e:
            self.logger.error(f"Error creating withdrawal req for user {user_obj.user_id} (PK: {user_obj.id}): {e}", exc_info=True)
            self.db.session.rollback()
            return None

    def process_withdrawal(self, withdrawal_id: int, approved: bool, admin_tg_id: int) -> bool:
        try:
            withdrawal_req = self.db.session.query(WithdrawalRequest).get(withdrawal_id)
            if not withdrawal_req:
                self.logger.warning(f"Withdrawal request ID {withdrawal_id} not found for processing by admin {admin_tg_id}.")
                return False
            
            user_obj = withdrawal_req.user 
            if not user_obj: 
                self.logger.error(f"User not found for withdrawal request ID {withdrawal_id}. Cannot process.")
                return False

            withdrawal_req.status = 'approved' if approved else 'rejected'
            withdrawal_req.processed_date = datetime.now()
            withdrawal_req.processed_by = admin_tg_id 

            action_log = "approved" if approved else "rejected"
            if approved:
                user_obj.withdrawals += withdrawal_req.amount
            else: 
                user_obj.balance += withdrawal_req.amount
            
            self.db.session.commit()
            self.logger.info(f"Withdrawal request ID {withdrawal_id} (User: {user_obj.user_id}) has been {action_log} by admin {admin_tg_id}.")
            return True
        except Exception as e:
            self.logger.error(f"Error processing withdrawal ID {withdrawal_id} by admin {admin_tg_id}: {e}", exc_info=True)
            self.db.session.rollback()
            return False
class MessageBuilder:
    @staticmethod
    def build_welcome_message(user: User, user_name: str) -> str:
        if user.total_earned >= 1000: status = "👑 VIP"
        elif user.total_earned >= 500: status = "🥇 Продвинутый"
        elif user.total_earned >= 100: status = "🥈 Активный"
        else: status = "🥉 Новичок"
        referrals_count = len(user.referrals) if hasattr(user, 'referrals') and user.referrals is not None else 0
        return f"""🚀 *Добро пожаловать, {user_name}!*
{status} • ID: `{user.user_id}`
💎 *Ваш профиль:*
├ Баланс: *{format_currency(user.balance)}*
├ Заработано: *{format_currency(user.total_earned)}*
├ Выведено: *{format_currency(user.withdrawals)}*
└ Рефералов: *{referrals_count}*
🎯 Выберите действие для продолжения:"""

    @staticmethod
    def build_stats_message(user: User) -> str:
        ref_count = len(user.referrals) if hasattr(user, 'referrals') else 0
        ref_earnings = sum(r.bonus_paid for r in user.referrals if hasattr(r, 'bonus_paid')) if hasattr(user, 'referrals') else 0
        investments = user.investments if hasattr(user, 'investments') else []
        invest_earnings = sum(i.total_profit for i in investments if hasattr(i, 'total_profit'))
        active_investments = len([i for i in investments if hasattr(i, 'is_finished') and not i.is_finished])
        total_invested = user.total_invested if hasattr(user, 'total_invested') else 0
        roi = (user.total_earned / max(total_invested, 1)) * 100 if total_invested > 0 else 0
        return f"""📊 *ДЕТАЛЬНАЯ СТАТИСТИКА*
💰 *Финансовый профиль:*
├ Баланс: *{format_currency(user.balance)}* | Всего заработано: *{format_currency(user.total_earned)}*
├ Выведено: *{format_currency(user.withdrawals)}* | ROI: *{roi:.1f}%*
👥 *Партнёрская программа:*
├ Приглашено: *{ref_count}* | Заработано с рефералов: *{format_currency(ref_earnings)}*
└ Средний доход с реферала: *{format_currency(ref_earnings / max(ref_count, 1))}*
📈 *Инвестиционная деятельность:*
├ Всего инвестировано: *{format_currency(total_invested)}*
├ Прибыль с инвестиций: *{format_currency(invest_earnings)}*
├ Активных планов: *{active_investments}* | Завершённых: *{len(investments) - active_investments}*
📅 *Активность:*
├ Дата регистрации: {user.join_date.strftime('%d.%m.%Y')}
└ Последний бонус: {user.last_bonus.strftime('%d.%m.%Y %H:%M') if user.last_bonus else 'Не получен'}"""

    @staticmethod
    def build_admin_panel_message(stats: Dict[str, Any]) -> str:
        return f"""👑 *ПАНЕЛЬ АДМИНИСТРАТОРА*
📊 *Пользователи:*
├ Всего: *{stats.get('total_users', 0):,}* | Активных 24ч: *{stats.get('active_users_24h', 'N/A'):,}*
├ Заблокировано: *{stats.get('blocked_users', 0):,}* | Новых сегодня: *{stats.get('new_today', 'N/A'):,}*
💰 *Финансы:*
├ Общий баланс: *{format_currency(stats.get('total_balance_all_users', 0))}*
├ Выплачено: *{format_currency(stats.get('total_withdrawals_approved', 0))}*
└ Инвестировано: *{format_currency(stats.get('total_invested_all_users', 0))}*
⚙️ *Заявки:*
└ На вывод (в ожидании): *{stats.get('pending_withdrawals_count',0):,}*
🕐 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}"""

    @staticmethod
    def build_bonus_message(amount: int, balance: int, streak: int = 1) -> str:
        return f"""🎁 *ЕЖЕДНЕВНЫЙ БОНУС ПОЛУЧЕН!*
💰 Начислено: *{format_currency(amount)}*
💎 Новый баланс: *{format_currency(balance)}*
🔥 Серия дней: *{streak}* (точный подсчет серии требует доработки)
⏰ Следующий бонус через 24 часа."""

    @staticmethod
    def build_info_message() -> str:
        return f"""💡 *КАК ЗАРАБОТАТЬ В БОТЕ*
🚀 *Основные способы:*
1️⃣ *Партнёрская программа:* Приглашайте друзей, получайте *{format_currency(REFERRAL_BONUS)}* за активного друга (подписка на {CHANNEL_LINK} + активность).
2️⃣ *Ежедневные бонусы:* *{format_currency(DAILY_BONUS)}* каждый день (серии в разработке).
3️⃣ *Инвестиционные планы:* (Примеры)
   ├ 🌱 Старт: от 100₽, 1.2%/день
   └ 👑 VIP: от 20,000₽, 3.5%/день
4️⃣ *Статусы:* Новичок 🥉 ... VIP 👑 (зависит от заработка).
💸 *Вывод средств:* Мин. *{format_currency(MIN_WITHDRAW)}*. Системы: Карта, QIWI, ЮMoney, USDT. Обработка до 24ч, комиссия 0%.
🎯 *Советы:* Ежедневные бонусы, активные друзья, инвестиции, новости канала ({CHANNEL_LINK})."""

class KeyboardBuilder:
    @staticmethod
    def build_main_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
        kb = [[InlineKeyboardButton("💎 Баланс", callback_data='balance'), InlineKeyboardButton("📊 Статистика", callback_data='stats')],
              [InlineKeyboardButton("🚀 Инвестиции", callback_data='investments'), InlineKeyboardButton("👥 Партнёры", callback_data='referral')],
              [InlineKeyboardButton("💸 Вывод средств", callback_data='withdraw'), InlineKeyboardButton("🎁 Ежедневный бонус", callback_data='bonus')],
              [InlineKeyboardButton("🏆 Рейтинг", callback_data='top'), InlineKeyboardButton("💡 Инфо", callback_data='info')],
              [InlineKeyboardButton("📢 Канал", url=CHANNEL_LINK), InlineKeyboardButton("📋 История", callback_data='history')]]
        if is_admin: kb.append([InlineKeyboardButton("👑 АДМИНКА", callback_data='admin_panel')])
        return InlineKeyboardMarkup(kb)

    @staticmethod
    def build_admin_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Статистика", callback_data='admin_stats'), InlineKeyboardButton("👤 Упр. Юзерами", callback_data='admin_users_manage')],
            [InlineKeyboardButton("📢 Рассылка", callback_data='admin_broadcast'), InlineKeyboardButton("✉️ ЛС Юзеру", callback_data='admin_send_to_user')],
            [InlineKeyboardButton("💰 Заявки Вывода", callback_data='admin_withdrawal_requests'), InlineKeyboardButton("📈 Упр. Инвест.", callback_data='admin_investments_manage')],
            [InlineKeyboardButton("🚫 Блок", callback_data='admin_user_block'), InlineKeyboardButton("✅ Разблок", callback_data='admin_user_unblock')],
            [InlineKeyboardButton("⚙️ Настройки", callback_data='admin_bot_settings'), InlineKeyboardButton("🏠 Главное Меню", callback_data='menu')]
        ])

    @staticmethod
    def build_payment_keyboard(amount: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Карта", callback_data=f'req_withdraw_card_{amount}'), InlineKeyboardButton("🥝 QIWI", callback_data=f'req_withdraw_qiwi_{amount}')],
            [InlineKeyboardButton("💛 ЮMoney", callback_data=f'req_withdraw_ymoney_{amount}'), InlineKeyboardButton("₿ USDT", callback_data=f'req_withdraw_usdt_{amount}')],
            [InlineKeyboardButton("🔙 Назад", callback_data='withdraw')]
        ])
    @staticmethod
    def build_back_keyboard(callback_data: str = 'menu') -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В главное меню", callback_data=callback_data)]])

    @staticmethod
    def build_confirmation_keyboard(action_prefix: str, item_id: Any = "", extra_data: str = "") -> InlineKeyboardMarkup:
        confirm_cb = f"{action_prefix}_{item_id}" + (f"_{extra_data}" if extra_data else "")
        cancel_destination = 'admin_panel' if 'admin' in action_prefix else 'menu'
        return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить", callback_data=confirm_cb),
                                      InlineKeyboardButton("❌ Отмена", callback_data=cancel_destination)]])
# --- КОНЕЦ ПРОПУЩЕННЫХ КЛАССОВ ---

class TelegramBot:
    def __init__(self):
        self.logger = logging.getLogger("TelegramBotApp") # Используем уже настроенный логгер
        try:
            self.db = Database() # Инициализация соединения с БД
        except Exception as e_db_init:
            self.logger.critical(f"CRITICAL: Failed to initialize Database connection: {e_db_init}", exc_info=True)
            # Если БД не инициализирована, бот не сможет работать.
            # Рассмотрите возможность отправки уведомления админам, если это возможно без БД.
            exit(1) # Или raise SystemExit("Database initialization failed")

        self.user_service = UserService(self.db)
        self.bonus_service = BonusService(self.db)
        self.withdrawal_service = WithdrawalService(self.db)
        self.logger.info("🧩 TelegramBot service components initialized successfully.")

    def setup_handlers(self, application: Application) -> None:
        # Убедитесь, что все обработчики - это async функции
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("admin", handle_admin_command)) # Должен быть async
        application.add_handler(CallbackQueryHandler(self.button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_message)) # Должен быть async
        self.logger.info("🛠️ Command, callback, and message handlers configured.")

    async def post_init(self, application: Application) -> None:
        try:
            self.db.init_db() # Создает таблицы, если их нет (метод должен быть идемпотентным)
            self.logger.info("🗄️ Database schema checked/initialized (post_init).")
            bot_info = await application.bot.get_me()
            self.logger.info(f"🤖 Bot @{bot_info.username} (ID: {bot_info.id}) is online and ready (post_init complete).")
        except Exception as e_post_init:
            self.logger.critical(f"💥 CRITICAL error during bot post_init (e.g., DB schema check): {e_post_init}", exc_info=True)
            # Это критическая ошибка, бот, вероятно, не сможет нормально функционировать.
            # Попытка уведомить админов (если TOKEN и ADMIN_IDS есть)
            if TOKEN and ADMIN_IDS and application and hasattr(application, 'bot'):
                error_msg_admin = f"🚨 CRITICAL POST_INIT ERROR for bot @{application.bot.username if hasattr(application.bot, 'username') else 'UnknownBot'}:\n{type(e_post_init).__name__}: {e_post_init}\nBot may not function correctly."
                for admin_id_notify in ADMIN_IDS:
                    try: await application.bot.send_message(admin_id_notify, error_msg_admin)
                    except Exception as e_send: self.logger.error(f"Failed to send post_init error to admin {admin_id_notify}: {e_send}")
            raise # Важно пробросить ошибку, чтобы PTB корректно обработала сбой

    async def cleanup(self, application: Application) -> None:
        self.logger.info("🧹 Bot cleanup process started...")
        try:
            if hasattr(self.db, 'session') and self.db.session and hasattr(self.db.session, 'close'):
                self.db.session.close()
                self.logger.info("🚪 Database session closed during cleanup.")
            # Другие действия по очистке, если нужны
        except Exception as e_cleanup:
            self.logger.error(f"⚠️ Error during bot cleanup: {e_cleanup}", exc_info=True)
        self.logger.info("🧼 Bot cleanup process finished.")


    async def handle_daily_bonus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id_tg = query.from_user.id
        self.logger.debug(f"User {user_id_tg} attempting daily bonus claim via callback '{query.data}'.")
        try:
            user_obj = self.db.get_user(user_id_tg)
            if not user_obj:
                await query.edit_message_text("❌ Пользователь не найден. Пожалуйста, начните с /start.", parse_mode=ParseMode.MARKDOWN)
                return

            can_claim, time_left = self.bonus_service.can_claim_daily_bonus(user_obj)
            if not can_claim:
                if time_left:
                    hours, remainder_seconds = divmod(int(time_left.total_seconds()), 3600)
                    minutes = remainder_seconds // 60
                    await query.answer(f"⏳ Следующий бонус будет доступен через {hours}ч {minutes}мин.", show_alert=True)
                else: 
                    await query.answer("⏳ Бонус пока недоступен. Попробуйте позже.", show_alert=True)
                return

            if self.bonus_service.claim_daily_bonus(user_obj):
                streak = self._calculate_bonus_streak(user_obj) # Заглушка, возвращает 1
                bonus_text_msg = MessageBuilder.build_bonus_message(DAILY_BONUS, user_obj.balance, streak)
                await query.edit_message_text(bonus_text_msg, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)
            else:
                await query.answer("❌ Не удалось начислить бонус. Возможно, вы уже получили его или произошла системная ошибка.", show_alert=True)
        except Exception as e:
            self.logger.error(f"Error in handle_daily_bonus for user {user_id_tg}: {e}", exc_info=True)
            await self._send_error_message(update, "Произошла ошибка при получении ежедневного бонуса.")

    def _calculate_bonus_streak(self, user: User) -> int:
        # ЗАГЛУШКА. Требует доработки с хранением состояния (например, поле User.bonus_streak и User.last_bonus_date)
        self.logger.debug(f"Bonus streak for user {user.user_id} (PK: {user.id}) calculated (placeholder returns 1).")
        return 1 

    async def show_admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        admin_user_id_tg = query.from_user.id
        self.logger.debug(f"Admin panel requested by admin {admin_user_id_tg} via callback '{query.data}'.")
        try:
            if not self.user_service.is_admin(admin_user_id_tg):
                await query.answer("❌ У вас нет прав для доступа к этой панели.", show_alert=True)
                return

            # Сбор статистики для админ-панели
            # Эти методы должны существовать в self.db или быть заменены на прямые запросы SQLAlchemy
            # Значения по умолчанию 'N/A' или 0 используются, если методы не найдены или вернули None
            stats_data = {
                'total_users': self.db.session.query(User).count() or 0,
                'active_users_24h': getattr(self.db, 'get_active_users_count_24h', lambda: 'N/A')(),
                'blocked_users': self.db.session.query(User).filter_by(is_blocked=True).count() or 0,
                'new_today': getattr(self.db, 'get_new_users_today_count', lambda: 'N/A')(),
                'total_balance_all_users': sum(u.balance for u in self.db.session.query(User.balance).all()) or 0,
                'total_withdrawals_approved': sum(w.amount for w in self.db.session.query(WithdrawalRequest.amount).filter_by(status='approved').all()) or 0,
                'total_invested_all_users': sum(u.total_invested for u in self.db.session.query(User.total_invested).all()) or 0,
                'pending_withdrawals_count': self.db.session.query(WithdrawalRequest).filter_by(status='pending').count() or 0
            }
            
            admin_panel_text_msg = MessageBuilder.build_admin_panel_message(stats_data)
            await query.edit_message_text(admin_panel_text_msg, reply_markup=KeyboardBuilder.build_admin_keyboard(), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            self.logger.error(f"Error in show_admin_panel for admin {admin_user_id_tg}: {e}", exc_info=True)
            await self._send_error_message(update, "Ошибка при загрузке данных для админ-панели.")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Используем update.effective_user для получения информации о пользователе
        eff_user = update.effective_user
        if not eff_user: 
            self.logger.warning("Start command received without effective_user object. Ignoring.")
            return

        user_id_tg = eff_user.id
        self.logger.info(f"/start command initiated by user_id: {user_id_tg}, username: {eff_user.username or 'N/A'}")
        
        try:
            # Проверка, если команда вызвана в групповом чате
            if update.message and update.message.chat.type != 'private':
                await update.message.reply_text(
                    f"ℹ️ Привет! Я работаю только в личных сообщениях. ID этого чата: `{update.message.chat.id}`", 
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            user_name_display = eff_user.first_name or eff_user.username or "Пользователь" # Отображаемое имя
            ref_arg_str = context.args[0] if context.args and len(context.args) > 0 else None # Аргумент реферала (может быть TG ID)

            # Проверка на блокировку пользователя
            if self.user_service.is_blocked(user_id_tg):
                blocked_text_msg = "🚫 *ДОСТУП ОГРАНИЧЕН*\n\nК сожалению, ваш аккаунт был заблокирован администрацией. " \
                                   "Если вы считаете, что это ошибка, обратитесь в службу поддержки." # Добавьте контакт поддержки
                if update.message: await update.message.reply_text(blocked_text_msg, parse_mode=ParseMode.MARKDOWN)
                elif update.callback_query: await update.callback_query.message.reply_text(blocked_text_msg, parse_mode=ParseMode.MARKDOWN)
                return

            # Получение или создание пользователя
            user_obj_db = self.db.get_user(user_id_tg) # get_user ищет по Telegram ID
            if not user_obj_db:
                self.logger.info(f"User {user_id_tg} not found in DB. Proceeding to create.")
                referrer_pk_for_creation = None # PK реферера
                if ref_arg_str and ref_arg_str.isdigit():
                    referrer_tg_id_from_arg = int(ref_arg_str)
                    if referrer_tg_id_from_arg != user_id_tg: # Не сам себе реферер
                        # Пытаемся найти реферера по его Telegram ID
                        temp_referrer_obj_db = self.db.get_user(referrer_tg_id_from_arg)
                        if temp_referrer_obj_db:
                            referrer_pk_for_creation = temp_referrer_obj_db.id # Сохраняем PK реферера
                            self.logger.info(f"Referrer TG ID {referrer_tg_id_from_arg} (PK: {referrer_pk_for_creation}) found for new user {user_id_tg}.")
                        else:
                             self.logger.warning(f"Referrer TG ID {referrer_tg_id_from_arg} from ref_arg not found in DB.")
                
                # Создаем пользователя, передавая Telegram ID и PK реферера (если найден)
                user_obj_db = await self.user_service.create_user(user_id_tg, referrer_pk_for_creation)
                if not user_obj_db: # Если создание пользователя не удалось
                    self.logger.error(f"Failed to create user profile for TG ID {user_id_tg} after attempting UserService.create_user.")
                    await self._send_error_message(update, "Не удалось создать ваш профиль в системе. Пожалуйста, попробуйте позже или обратитесь в поддержку.")
                    return
                self.logger.info(f"User {user_id_tg} (PK: {user_obj_db.id}) successfully created in DB.")
            
            # Проверка подписки на канал (если CHANNEL_ID настроен)
            if CHANNEL_ID and not self.user_service.is_admin(user_id_tg):
                # check_channel_subscription должна принимать context, user_id_tg, CHANNEL_ID
                is_subscribed_now = await check_channel_subscription(context, user_id_tg, CHANNEL_ID) 
                if not is_subscribed_now:
                    # show_channel_check должна принимать update, context, CHANNEL_ID, CHANNEL_LINK
                    await show_channel_check(update, context, CHANNEL_ID, CHANNEL_LINK) 
                    return # Прерываем выполнение /start, пока пользователь не подпишется
            
            # Обновляем статус подписки в БД, если пользователь подписался
            if CHANNEL_ID and hasattr(user_obj_db, 'channel_joined') and not user_obj_db.channel_joined:
                # Повторно проверяем, т.к. пользователь мог подписаться и нажать "Проверить"
                if await check_channel_subscription(context, user_id_tg, CHANNEL_ID):
                    user_obj_db.channel_joined = True
                    self.db.session.commit()
                    self.logger.info(f"User {user_id_tg} (PK: {user_obj_db.id}) channel subscription status updated to True in DB.")

            # Формируем и отправляем приветственное сообщение
            welcome_text_msg = MessageBuilder.build_welcome_message(user_obj_db, user_name_display)
            main_keyboard = KeyboardBuilder.build_main_keyboard(self.user_service.is_admin(user_id_tg))

            if update.callback_query: # Если /start был вызван через callback (например, кнопка "меню")
                await update.callback_query.edit_message_text(welcome_text_msg, reply_markup=main_keyboard, parse_mode=ParseMode.MARKDOWN)
            elif update.message: # Если /start был вызван как команда
                await update.message.reply_text(welcome_text_msg, reply_markup=main_keyboard, parse_mode=ParseMode.MARKDOWN)

        except Exception as e_start:
            self.logger.error(f"CRITICAL error in /start command for user {user_id_tg}: {e_start}", exc_info=True)
            await self._send_error_message(update, "Произошла серьезная ошибка при инициализации. Пожалуйста, сообщите администратору.")

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data: 
            self.logger.warning("Button_handler received an update without query or query.data.")
            if query: await query.answer() # Отвечаем на пустой callback, если он есть
            return
            
        await query.answer() # Отвечаем на callback немедленно, чтобы кнопка перестала "грузиться"
        
        user_id_tg = query.from_user.id
        callback_data_received = query.data
        self.logger.info(f"Button '{callback_data_received}' pressed by user {user_id_tg}.")

        try:
            # Проверка на блокировку
            if self.user_service.is_blocked(user_id_tg):
                await query.answer("🚫 Ваш аккаунт заблокирован. Доступ к функциям ограничен.", show_alert=True)
                # Можно также отредактировать сообщение, если это уместно, например:
                # if query.message: 
                #     await query.edit_message_text("🚫 Доступ заблокирован.", reply_markup=None)
                return

            # Получение объекта пользователя из БД
            user_obj_db = self.db.get_user(user_id_tg)
            if not user_obj_db:
                self.logger.warning(f"User {user_id_tg} not found in DB during button press '{callback_data_received}'. Sending to /start.")
                await query.edit_message_text(
                    "❌ Ваш профиль не найден. Пожалуйста, перезапустите бота командой /start.", 
                    reply_markup=KeyboardBuilder.build_back_keyboard('menu') # 'menu' обычно ведет на /start
                )
                return

            # Проверка подписки на канал для всех действий, кроме 'check_subscription' и если пользователь не админ
            if CHANNEL_ID and callback_data_received != 'check_subscription' and not self.user_service.is_admin(user_id_tg):
                if not await check_channel_subscription(context, user_id_tg, CHANNEL_ID):
                    # show_channel_check должен сам обработать update (отредактировать сообщение или отправить новое)
                    await show_channel_check(update, context, CHANNEL_ID, CHANNEL_LINK)
                    return # Прерываем дальнейшую обработку callback'а
            
            # Маршрутизация callback'а
            await self._route_callback(update, context, callback_data_received)

        except TelegramError as te_button: # Ошибки Telegram API
            self.logger.error(f"Telegram API Error during button '{callback_data_received}' processing for user {user_id_tg}: {te_button.message}", exc_info=False) # exc_info=False чтобы не спамить полным трейсбеком на частые API ошибки
            # Пользователю можно сообщить о временной проблеме
            try:
                await query.answer("⚠️ Произошла ошибка при связи с Telegram. Пожалуйста, попробуйте еще раз.", show_alert=True)
            except Exception: pass # Если даже answer не удался
        except Exception as e_button: # Другие непредвиденные ошибки
            self.logger.error(f"Unexpected Error during button '{callback_data_received}' processing for user {user_id_tg}: {e_button}", exc_info=True)
            await self._send_error_message(update, "При обработке вашего запроса произошла внутренняя ошибка.")

    async def _route_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
        user_id_tg = update.effective_user.id
        is_current_user_admin = self.user_service.is_admin(user_id_tg)

        # Словарь простых маршрутов (без параметров в callback_data)
        # Формат: 'callback_data': (handler_function, needs_admin_rights)
        route_actions = {
            'balance': (show_balance, False), 
            'stats': (self._show_user_stats, False),
            'investments': (show_investments, False), 
            'withdraw': (handle_withdraw_request, False), # Этот хендлер может сам запросить сумму
            'bonus': (self.handle_daily_bonus, False), 
            'referral': (show_referral_program, False),
            'top': (self._show_top_users, False), 
            'info': (self._show_info, False),
            'history': (self._show_withdrawal_history, False), 
            'menu': (self.start, False),
            'check_subscription': (self._handle_check_subscription, False),
            # Админские простые маршруты
            'admin_panel': (self.show_admin_panel, True),
            'admin_stats': (self._show_detailed_stats, True),
            # Другие админские действия, если они не требуют параметров из callback
            'admin_users_manage': (handle_admin_message, True), # Пример: передаем в общий админский обработчик
            'admin_broadcast': (handle_admin_message, True),
            'admin_send_to_user': (handle_admin_message, True),
            'admin_withdrawal_requests': (handle_admin_message, True),
            'admin_investments_manage': (handle_admin_message, True),
            'admin_user_block': (handle_admin_message, True),
            'admin_user_unblock': (handle_admin_message, True),
            'admin_bot_settings': (handle_admin_message, True),
        }

        if data in route_actions:
            handler, needs_admin = route_actions[data]
            if needs_admin and not is_current_user_admin:
                await update.callback_query.answer("❌ У вас нет прав для этого действия.", show_alert=True)
                return
            await handler(update, context) # Вызываем соответствующий обработчик
            return # Завершаем, если маршрут найден здесь

        # Обработка callback'ов с параметрами (например, для инвестиций, вывода конкретной суммы)
        # Эти префиксы должны быть уникальными
        if data.startswith(('invest_', 'confirm_invest_', 'calc_')):
            await handle_investment_request(update, context) # Этот обработчик должен сам парсить data
        elif data.startswith('req_withdraw_'): # Формат: req_withdraw_METHOD_AMOUNT
            parts = data.split('_') 
            if len(parts) == 4 and parts[3].isdigit(): # req_withdraw_card_1000
                method_str, amount_int = parts[2], int(parts[3])
                # Этот обработчик должен запросить у пользователя реквизиты для вывода
                await handle_payment_details(update, context, payment_method=method_str, amount=amount_int)
            else:
                self.logger.warning(f"Invalid 'req_withdraw_' callback format: {data} from user {user_id_tg}")
                await self._handle_unknown_callback(update, context)
        
        # Пример обработки callback'ов подтверждения для админа
        elif data.startswith('confirm_'): # Например, confirm_wd_approve_123 или confirm_wd_reject_123
            if is_current_user_admin:
                action_parts = data.split('_') # confirm, target, operation, item_id
                if len(action_parts) >= 4:
                    target, operation, item_id_str = action_parts[1], action_parts[2], action_parts[3]
                    if item_id_str.isdigit():
                        item_id = int(item_id_str)
                        if target == 'wd': # Withdrawal
                            if operation == 'approve':
                                success = self.withdrawal_service.process_withdrawal(item_id, True, user_id_tg)
                                await update.callback_query.answer(f"Заявка #{item_id} {'одобрена' if success else 'ошибка одобрения'}.", show_alert=True)
                                # TODO: Уведомить пользователя об одобрении/отклонении
                                # TODO: Обновить сообщение админа (например, убрать кнопки для этой заявки)
                            elif operation == 'reject':
                                success = self.withdrawal_service.process_withdrawal(item_id, False, user_id_tg)
                                await update.callback_query.answer(f"Заявка #{item_id} {'отклонена' if success else 'ошибка отклонения'}.", show_alert=True)
                            else: await self._handle_unknown_callback(update, context)
                        else: await self._handle_unknown_callback(update, context) # Неизвестная цель подтверждения
                    else: await self._handle_unknown_callback(update, context) # ID не число
                else: await self._handle_unknown_callback(update, context) # Неверный формат confirm
            else: await update.callback_query.answer("❌ Доступ запрещен.", show_alert=True)
        
        else: # Если ни один из маршрутов не подошел
            await self._handle_unknown_callback(update, context)

    async def _handle_check_subscription(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id_tg = update.effective_user.id
        self.logger.debug(f"User {user_id_tg} initiated subscription check via callback '{update.callback_query.data}'.")
        
        if not CHANNEL_ID: # Если ID канала не настроен, проверка невозможна
            self.logger.warning("CHANNEL_ID is not configured. Cannot perform subscription check.")
            await update.callback_query.answer("Проверка подписки временно недоступна (канал не настроен).", show_alert=True)
            # Можно вернуть пользователя в главное меню или оставить как есть
            # await self.start(update, context) 
            return

        # check_channel_subscription должна быть async и принимать context, user_id_tg, CHANNEL_ID
        if await check_channel_subscription(context, user_id_tg, CHANNEL_ID):
            user_obj_db = self.db.get_user(user_id_tg)
            if user_obj_db and hasattr(user_obj_db, 'channel_joined') and not user_obj_db.channel_joined:
                user_obj_db.channel_joined = True
                self.db.session.commit()
                self.logger.info(f"User {user_id_tg} (PK: {user_obj_db.id}) subscription confirmed, DB status updated.")
            
            await update.callback_query.answer("✅ Спасибо за подписку! Доступ к функциям бота открыт.", show_alert=False)
            await self.start(update, context) # Перенаправляем пользователя на главный экран (в /start)
        else:
            await update.callback_query.answer(
                f"Вы все еще не подписаны на наш канал {CHANNEL_LINK}. "
                "Пожалуйста, подпишитесь и нажмите кнопку 'Проверить подписку' еще раз.", 
                show_alert=True
            )
            # Можно не показывать снова show_channel_check, т.к. пользователь уже видит это сообщение с alert.

    async def _show_user_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id_tg = query.from_user.id
        self.logger.debug(f"User stats requested by user {user_id_tg} via callback '{query.data}'.")
        user_obj_db = self.db.get_user(user_id_tg)
        if not user_obj_db:
            await query.edit_message_text("❌ Ваш профиль не найден. Попробуйте перезапустить бота: /start.", parse_mode=ParseMode.MARKDOWN)
            return
        stats_text_msg = MessageBuilder.build_stats_message(user_obj_db)
        await query.edit_message_text(stats_text_msg, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)

    async def _show_detailed_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        admin_user_id_tg = query.from_user.id
        self.logger.debug(f"Detailed system stats requested by admin {admin_user_id_tg} via callback '{query.data}'.")
        try:
            # Сборка статистики. Методы get_active_users_count_24h и т.д. должны быть в self.db
            # или заменены на прямые запросы SQLAlchemy.
            stats_payload = {
                'total_users': self.db.session.query(User).count() or 0,
                'active_users_24h': getattr(self.db, 'get_active_users_count_24h', lambda: "N/A")(),
                'blocked_users': self.db.session.query(User).filter_by(is_blocked=True).count() or 0,
                'new_today': getattr(self.db, 'get_new_users_today_count', lambda: "N/A")(),
                'subscribed_users': getattr(self.db, 'get_subscribed_users_count', lambda: "N/A")(),
                'total_balance_all_users': sum(u.balance for u in self.db.session.query(User.balance).all()) or 0,
                'total_withdrawals_approved': sum(w.amount for w in self.db.session.query(WithdrawalRequest.amount).filter_by(status='approved').all()) or 0,
                'total_invested_all_users': sum(u.total_invested for u in self.db.session.query(User.total_invested).all()) or 0,
                'pending_withdrawals_count': self.db.session.query(WithdrawalRequest).filter_by(status='pending').count() or 0,
                # 'total_referrals': getattr(self.db, 'get_total_referrals_count', lambda: "N/A")(),
                # 'avg_earnings': getattr(self.db, 'get_average_earnings_per_user', lambda: "N/A")(),
            }
            # Адаптируйте MessageBuilder.build_admin_panel_message, чтобы он использовал эти ключи
            detailed_stats_text_msg = MessageBuilder.build_admin_panel_message(stats_payload) # Переименовал для ясности
            await query.edit_message_text(detailed_stats_text_msg, reply_markup=KeyboardBuilder.build_back_keyboard('admin_panel'), parse_mode=ParseMode.MARKDOWN)
        except Exception as e_det_stats:
            self.logger.error(f"Error showing detailed system stats for admin {admin_user_id_tg}: {e_det_stats}", exc_info=True)
            await self._send_error_message(update, "Ошибка при загрузке детальной статистики системы.")

    async def _show_top_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id_tg = query.from_user.id
        self.logger.debug(f"Top users list requested by user {user_id_tg} via callback '{query.data}'.")
        try:
            # Запрос к БД за топ-10 пользователями
            top_users_list_db = self.db.session.query(User)\
                .order_by(User.total_earned.desc(), User.balance.desc())\
                .limit(10).all()
            
            top_text_msg = "🏆 *ТОП-10 УСПЕШНЫХ ПОЛЬЗОВАТЕЛЕЙ*\n\n"
            medals = ["🥇", "🥈", "🥉"] + [f"{i}️⃣" for i in range(4, 11)]

            if not top_users_list_db:
                top_text_msg += "😔 Пока нет пользователей в рейтинге. Будьте первым!"
            else:
                for i, user_db_obj_top in enumerate(top_users_list_db):
                    display_name_top = f"ID {user_db_obj_top.user_id}" # Имя по умолчанию
                    try:
                        chat_info_top = await context.bot.get_chat(user_db_obj_top.user_id)
                        name_candidate = chat_info_top.first_name or chat_info_top.username
                        if name_candidate: display_name_top = name_candidate
                        # Обрезка длинных имен
                        display_name_top = display_name_top[:20].rstrip() + ("..." if len(display_name_top) > 20 else "")
                    except TelegramError as te_top_user_chat: 
                        self.logger.warning(f"TelegramError getting chat info for top user {user_db_obj_top.user_id}: {te_top_user_chat.message}")
                    except Exception as e_top_user_chat_generic: 
                        self.logger.error(f"Unexpected error getting chat info for top user {user_db_obj_top.user_id}: {e_top_user_chat_generic}", exc_info=False)
                    
                    refs_count_top = len(user_db_obj_top.referrals) if hasattr(user_db_obj_top, 'referrals') else 0
                    active_inv_count_top = 0
                    if hasattr(user_db_obj_top, 'investments'):
                        active_inv_count_top = len([inv for inv in user_db_obj_top.investments if hasattr(inv, 'is_finished') and not inv.is_finished])
                    
                    top_text_msg += f"{medals[i]} *{display_name_top}*\n"
                    top_text_msg += f"  ├ Заработано: *{format_currency(user_db_obj_top.total_earned)}*\n"
                    top_text_msg += f"  ├ Рефералов: *{refs_count_top}*\n"
                    top_text_msg += f"  └ Активных инвестиций: *{active_inv_count_top}*\n\n"
            
            top_text_msg += "\n💡 *Станьте частью топа! Приглашайте друзей, инвестируйте и зарабатывайте больше.*"
            await query.edit_message_text(top_text_msg, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)
        except Exception as e_top_users:
            self.logger.error(f"Error in _show_top_users for user {user_id_tg}: {e_top_users}", exc_info=True)
            await self._send_error_message(update, "Произошла ошибка при загрузке рейтинга пользователей.")

    async def _show_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id_tg = query.from_user.id
        self.logger.debug(f"Info section requested by user {user_id_tg} via callback '{query.data}'.")
        info_text_msg = MessageBuilder.build_info_message() # Убедитесь, что все константы в ней доступны
        await query.edit_message_text(info_text_msg, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)

    async def _show_withdrawal_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id_tg = query.from_user.id # Telegram ID
        self.logger.debug(f"Withdrawal history requested by user {user_id_tg} via callback '{query.data}'.")
        try:
            user_obj_db = self.db.get_user(user_id_tg) # Получаем объект User по Telegram ID
            if not user_obj_db:
                await query.edit_message_text("❌ Ваш профиль не найден. Попробуйте /start.", parse_mode=ParseMode.MARKDOWN)
                return

            # WithdrawalRequest.user_id должен быть FK на User.id (первичный ключ таблицы users)
            withdrawals_history_list = (
                self.db.session.query(WithdrawalRequest)
                .filter_by(user_id=user_obj_db.id) # Фильтруем по PK пользователя 
                .order_by(WithdrawalRequest.date.desc())
                .limit(10) # Показываем только последние 10 заявок
                .all()
            )
            
            if not withdrawals_history_list:
                history_text_msg = f"📋 *ИСТОРИЯ ВЫВОДОВ*\n\n" \
                                   f"❌ У вас пока нет заявок на вывод средств.\n\n" \
                                   f"💡 Минимальная сумма для вывода: {format_currency(MIN_WITHDRAW)}"
            else:
                history_text_msg = "📋 *ИСТОРИЯ ВЫВОДОВ* (последние {} из {} всего):\n\n".format(
                    len(withdrawals_history_list),
                    self.db.session.query(WithdrawalRequest).filter_by(user_id=user_obj_db.id).count() # Общее кол-во для информации
                )
                
                # Статистика по показанным заявкам
                # total_requested_shown = sum(w.amount for w in withdrawals_history_list)
                # approved_shown_count = len([w for w in withdrawals_history_list if w.status == 'approved'])
                # history_text_msg += f"📊 *Показано заявок:* {len(withdrawals_history_list)}\n"
                # history_text_msg += f"├ Одобрено из них: *{approved_shown_count}*\n"
                # history_text_msg += f"└ Сумма показанных: *{format_currency(total_requested_shown)}*\n\n"
                
                for w_req_hist in withdrawals_history_list:
                    status_emoji_map = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}
                    status_text_map = {'pending': 'В обработке', 'approved': 'Одобрена', 'rejected': 'Отклонена'}
                    
                    emoji = status_emoji_map.get(w_req_hist.status, '❓')
                    status_str = status_text_map.get(w_req_hist.status, 'Неизвестный статус')
                    
                    history_text_msg += f"🆔 *Заявка #{w_req_hist.id}* | Сумма: *{format_currency(w_req_hist.amount)}*\n"
                    history_text_msg += f"├ Система: *{w_req_hist.method.upper()}* | Дата: {w_req_hist.date.strftime('%d.%m.%y %H:%M')}\n"
                    history_text_msg += f"└ Статус: {emoji} {status_str}\n\n"

            await query.edit_message_text(history_text_msg, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)
        except Exception as e_wd_hist:
            self.logger.error(f"Error in _show_withdrawal_history for user {user_id_tg}: {e_wd_hist}", exc_info=True)
            await self._send_error_message(update, "Произошла ошибка при загрузке вашей истории выводов.")

    async def _handle_unknown_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        self.logger.warning(f"Unknown callback_data '{query.data}' received from user {query.from_user.id}. No action defined.")
        await query.answer("❓ Неизвестная команда или это действие было отменено системой.", show_alert=True)
        # Можно не перенаправлять в меню, чтобы пользователь видел текущее сообщение,
        # или перенаправить, если это более логично:
        # await self.start(update, context) 

    async def _send_error_message(self, update: Update, error_text_for_user: str) -> None:
        # Генерируем короткий ID для этой конкретной ошибки, чтобы его можно было найти в логах
        error_instance_guid = os.urandom(4).hex().upper() 
        
        # Логгируем ошибку с GUID
        self.logger.error(f"Error to be shown to user (Error GUID: {error_instance_guid}): '{error_text_for_user}'. "
                          f"Triggered by user: {update.effective_user.id if update.effective_user else 'Unknown'}.")
        
        # Формируем сообщение для пользователя
        user_facing_message = f"❌ {error_text_for_user}\n\n" \
                              f"Пожалуйста, попробуйте выполнить действие еще раз немного позже. " \
                              f"Если ошибка будет повторяться, свяжитесь со службой поддержки и сообщите " \
                              f"код ошибки: `{error_instance_guid}`"
        
        default_keyboard = KeyboardBuilder.build_back_keyboard('menu')
        try:
            if update.callback_query and update.callback_query.message:
                # Пытаемся отредактировать существующее сообщение, если это был callback
                await update.callback_query.edit_message_text(
                    user_facing_message, 
                    reply_markup=default_keyboard, 
                    parse_mode=ParseMode.MARKDOWN
                )
            elif update.message:
                # Отправляем новое сообщение, если это была команда или текстовое сообщение
                await update.message.reply_text(
                    user_facing_message, 
                    reply_markup=default_keyboard, 
                    parse_mode=ParseMode.MARKDOWN
                )
            elif update.callback_query: 
                # Если это callback, но сообщение не может быть отредактировано (например, оно слишком старое)
                # Отвечаем на сам callback и отправляем новое сообщение в чат.
                await update.callback_query.answer(error_text_for_user, show_alert=True) # Короткое уведомление
                if update.effective_chat: # Убедимся, что есть чат для отправки
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id, 
                        text=user_facing_message, 
                        reply_markup=default_keyboard, 
                        parse_mode=ParseMode.MARKDOWN
                    )
        except TelegramError as te_send_err: 
            # Ошибка при отправке самого сообщения об ошибке (например, бот заблокирован пользователем)
            self.logger.error(f"TelegramError while trying to send error message (GUID: {error_instance_guid}) to user: {te_send_err.message}")
        except Exception as e_send_err_generic: 
            self.logger.error(f"Unexpected error while trying to send error message (GUID: {error_instance_guid}) to user: {e_send_err_generic}", exc_info=True)

# --- ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА БОТА ---

async def main():
    # Логгер app_logger уже инициализирован глобально при импорте BotLogger.setup_logging()
    
    ptb_application_instance = None # Для доступа в finally
    cron_pinger_instance = None # Для доступа в finally
    bot_service_instance = None # Для доступа в finally

    try:
        app_logger.info("🏁 Application bootstrap sequence initiated...")
        
        # Создаем экземпляр нашего основного класса TelegramBot, который содержит всю бизнес-логику
        # и инициализирует подключение к БД и другие сервисы.
        bot_service_instance = TelegramBot() 
        
        # TOKEN должен быть уже загружен и проверен на этапе импорта настроек.
        # Если его нет, программа уже должна была завершиться.
        # Но на всякий случай, еще одна проверка не помешает.
        if not TOKEN: 
            app_logger.critical("❌ FATAL: TELEGRAM_BOT_TOKEN is not defined. Cannot proceed. Exiting application.")
            return 

        # Создаем экземпляр Application из библиотеки python-telegram-bot
        ptb_application_instance = Application.builder().token(TOKEN).build()
        
        # Настраиваем обработчики команд, callback'ов и сообщений
        bot_service_instance.setup_handlers(ptb_application_instance)
        
        # Настраиваем хуки жизненного цикла PTB (post_init, post_shutdown)
        # Эти методы будут вызваны PTB автоматически при старте и остановке.
        ptb_application_instance.post_init = bot_service_instance.post_init
        ptb_application_instance.post_shutdown = bot_service_instance.cleanup
        
        # Получаем URL приложения (важно для режима Webhook на Render)
        render_external_app_url = os.getenv('RENDER_EXTERNAL_URL') 
        
        # --- ОПРЕДЕЛЕНИЕ РЕЖИМА РАБОТЫ: WEBHOOK (RENDER) ИЛИ POLLING (LOCAL) ---
        if render_external_app_url and os.getenv('RENDER'): # RENDER - это стандартная переменная окружения на Render
            app_logger.info(f"📡 Detected Render environment (URL: {render_external_app_url}). Starting in Webhook mode.")
            
            # Render предоставляет порт через переменную окружения PORT
            server_listen_port_str = os.getenv('PORT') 
            if not server_listen_port_str or not server_listen_port_str.isdigit():
                app_logger.critical(f"❌ FATAL: Environment variable PORT is not set or is invalid ('{server_listen_port_str}') for Render. Webhook server cannot start. Exiting.")
                return
            server_listen_port = int(server_listen_port_str)

            # Путь для вебхука. Использование токена делает его "секретным".
            webhook_url_path_segment = TOKEN 
            # Полный URL, который будет зарегистрирован в Telegram API для получения обновлений
            full_webhook_url_to_register = f"{render_external_app_url.rstrip('/')}/{webhook_url_path_segment}"
            
            app_logger.info(f"Webhook configuration: URL for Telegram API -> {full_webhook_url_to_register}")
            app_logger.info(f"Internal Webhook server will listen on -> 0.0.0.0:{server_listen_port}, Path -> /{webhook_url_path_segment}")
            
            # Запуск Cron сервера (пингера), если он импортирован и настроен
            # CronServer используется для "пробуждения" сервиса на Render, если это необходимо (хотя Render сам пингует Web Services)
            # или для выполнения других периодических задач.
            if 'CronServer' in globals() and CronServer: # Проверяем, что класс CronServer доступен
                try:
                    # Интервал пингов можно также вынести в настройки/переменные окружения
                    ping_interval_seconds = int(os.getenv("CRON_PING_INTERVAL_SECONDS", 300)) # По умолчанию 5 минут
                    cron_pinger_instance = CronServer(render_external_app_url, interval=ping_interval_seconds) 
                    cron_pinger_instance.start() # Синхронный вызов start(), который запускает async задачу
                    # Лог о запуске уже есть внутри CronServer.start()
                except Exception as e_cron_start_webhook:
                    app_logger.warning(f"⚠️ Failed to start CronServer in Webhook mode: {e_cron_start_webhook}", exc_info=True)
            
            # Запускаем PTB в режиме webhook. Это блокирующий вызов, который запустит веб-сервер.
            await ptb_application_instance.run_webhook(
                listen="0.0.0.0", # Важно: слушать на всех доступных сетевых интерфейсах
                port=server_listen_port,
                url_path=webhook_url_path_segment, # Локальный путь, который слушает наш сервер
                webhook_url=full_webhook_url_to_register, # Полный URL, который регистрируется в Telegram API
                drop_pending_updates=True, # Удаляем "старые" обновления при старте
                # secret_token="YOUR_ACTUAL_SECRET_TOKEN_HERE" # Опционально: для дополнительной верификации запросов от Telegram (X-Telegram-Bot-Api-Secret-Token)
            )
            # Код после run_webhook не выполнится, пока сервер работает (или не произойдет ошибка).
            
        else:
            # Режим Polling для локальной разработки или если переменные для Render не установлены.
            app_logger.info("🔄 Render environment not detected or RENDER_EXTERNAL_URL not set. Starting in Polling mode.")
            
            # Перед запуском polling, важно удалить любой существующий вебхук, если он был установлен ранее.
            app_logger.info("Attempting to delete any existing webhook settings from Telegram API...")
            try:
                await ptb_application_instance.bot.delete_webhook(drop_pending_updates=True)
                app_logger.info("Webhook (if any was set) successfully deleted from Telegram.")
            except Exception as e_del_webhook:
                app_logger.warning(f"Could not delete webhook (this is often normal if none was set): {e_del_webhook}")

            # Запуск Cron сервера (если нужен локально, например, для пинга ngrok URL)
            local_dev_ping_url = os.getenv('LOCAL_DEV_PING_URL') # URL для пинга в режиме разработки (e.g., ngrok)
            if local_dev_ping_url and 'CronServer' in globals() and CronServer:
                 try:
                    ping_interval_dev = int(os.getenv("CRON_PING_INTERVAL_DEV_SECONDS", 600)) # 10 минут для dev
                    cron_pinger_instance = CronServer(local_dev_ping_url, interval=ping_interval_dev)
                    cron_pinger_instance.start()
                 except Exception as e_cron_start_local:
                    app_logger.warning(f"⚠️ Failed to start CronServer in Polling mode (local): {e_cron_start_local}", exc_info=True)
            
            # Запускаем PTB в режиме polling. Это блокирующий вызов.
            app_logger.info("Starting polling for updates from Telegram...")
            await ptb_application_instance.run_polling(
                drop_pending_updates=True,    # Удаляем "старые" обновления
                poll_interval=0.5,            # Как часто (в секундах) бот будет проверять наличие новых обновлений
                timeout=10,                   # Таймаут для одного запроса getUpdates (в секундах)
                # read_timeout, write_timeout, connect_timeout - для более тонкой настройки HTTP запросов
            )
            # Код после run_polling не выполнится, пока бот работает.
        
    except KeyboardInterrupt:
        app_logger.info("🛑 Bot manually stopped by user (KeyboardInterrupt). Initiating graceful shutdown...")
    except SystemExit as se: # Ловим явные вызовы exit()
        app_logger.info(f"Application exited with SystemExit code {se.code}.")
    except Exception as e_main_loop: # Ловим любые другие неперехваченные исключения на верхнем уровне
        app_logger.critical(f"💥 CRITICAL UNHANDLED ERROR in main application execution: {e_main_loop}", exc_info=True)
        # Попытка уведомить администраторов о критической ошибке, если это возможно
        if ptb_application_instance and hasattr(ptb_application_instance, 'bot') and TOKEN and ADMIN_IDS:
            error_report_admin_msg = (
                f"🚨 *КРИТИЧЕСКАЯ ОШИБКА БОТА*\n\n"
                f"Бот @{ptb_application_instance.bot.username if hasattr(ptb_application_instance.bot, 'username') else 'UnknownBot'} "
                f"был аварийно остановлен из-за непредвиденной ошибки:\n"
                f"`{type(e_main_loop).__name__}: {str(e_main_loop)}`\n\n"
                f"Проверьте логи сервера для получения подробной информации. Бот требует перезапуска."
            )
            for admin_tg_id_notify in ADMIN_IDS:
                try:
                    # Используем context.bot.send_message если бы у нас был context,
                    # но здесь у нас есть ptb_application_instance.bot
                    await ptb_application_instance.bot.send_message(
                        chat_id=admin_tg_id_notify, 
                        text=error_report_admin_msg, 
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e_send_admin_notification:
                    app_logger.error(f"Failed to send critical error notification to admin {admin_tg_id_notify}: {e_send_admin_notification}")
    finally:
        app_logger.info("🧼 Initiating final cleanup procedures before application exit...")
        
        # Останавливаем CronPinger, если он был запущен
        if cron_pinger_instance and hasattr(cron_pinger_instance, 'stop'):
            app_logger.info("Attempting to stop CronPinger server...")
            try:
                # Метод stop у CronServer теперь асинхронный
                await cron_pinger_instance.stop() 
                # Сообщение об успешной остановке уже есть внутри CronServer.stop()
            except Exception as e_cron_stop_final_cleanup:
                app_logger.error(f"Error during CronPinger server stop in final cleanup: {e_cron_stop_final_cleanup}", exc_info=True)
        
        # PTB v20+ автоматически обрабатывает остановку application (включая вызов post_shutdown хука),
        # когда run_polling/run_webhook завершаются (например, по KeyboardInterrupt или ошибке).
        # Явный вызов ptb_application_instance.stop() или ptb_application_instance.shutdown() здесь обычно не нужен
        # и может привести к ошибкам "event loop is closed", если сделан не в том месте или не тем способом.
        # PTB сама вызовет cleanup() метод нашего TelegramBot через post_shutdown хук.
        
        app_logger.info("🚪 Application shutdown process has finished.")

# --- ТОЧКА ВХОДА В ПРИЛОЖЕНИЕ ---
if __name__ == '__main__':
    # Этот блок кода выполняется только при прямом запуске файла (например, `python bot.py`)
    
    # Попытка загрузки переменных окружения из .env файла (полезно для локальной разработки)
    # Для этого должна быть установлена библиотека python-dotenv: pip install python-dotenv
    try:
        from dotenv import load_dotenv
        if load_dotenv(verbose=True): # verbose=True выведет путь к загруженному .env файлу
             app_logger.info("Successfully loaded environment variables from .env file.")
        else:
             app_logger.info("No .env file found or it's empty. Using system-level environment variables if set.")
    except ImportError:
        app_logger.info("python-dotenv library is not installed. Skipping .env file loading. "
                        "Ensure all required environment variables are set at the system level or in config.settings.")
    except Exception as e_dotenv:
        app_logger.warning(f"An error occurred while trying to load .env file: {e_dotenv}")

    # Финальная проверка наличия TOKEN перед запуском.
    # TOKEN должен быть уже определен либо из config.settings, либо из переменных окружения на этом этапе.
    if not TOKEN:
        app_logger.critical("❌ FATAL ERROR: TELEGRAM_BOT_TOKEN is not set after all configuration attempts. Bot cannot start.")
        app_logger.critical("Please ensure TOKEN is correctly set in config/settings.py, your .env file, or as a system environment variable.")
        exit(1) # Принудительное завершение программы, если нет токена
    
    app_logger.info(f"TOKEN found. Bot Admin IDs: {ADMIN_IDS if ADMIN_IDS else 'Not configured'}.")
    app_logger.info(f"Channel ID for subscription check: {CHANNEL_ID or 'Not configured'}.")

    # Запускаем асинхронную функцию main()
    try:
        asyncio.run(main())
    except RuntimeError as e_async_run:
        # Эта ошибка может возникнуть, если asyncio.run() вызывается, когда цикл уже запущен,
        # что маловероятно при правильной структуре, но лучше обработать.
        app_logger.critical(f"RuntimeError during asyncio.run(main()): {e_async_run}. "
                            "This might indicate an issue with event loop management.", exc_info=True)
