import logging
import os
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import TelegramError

# Импортируем настройки и утилиты
# Убедитесь, что все переменные (TOKEN, ADMIN_IDS, etc.) определены в config.settings.py
# и этот файл существует в правильном месте относительно bot.py
# Например, если bot.py в корне, то config/settings.py
try:
    from config.settings import *
except ImportError:
    print("Ошибка: Не удалось импортировать config.settings. Убедитесь, что файл существует и доступен.")
    # Попытка загрузить критически важные переменные из окружения, если импорт не удался
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(",") if admin_id.strip().isdigit()] if ADMIN_IDS_STR else []
    # Загрузка других констант с значениями по умолчанию, если они не найдены
    REFERRAL_BONUS = int(os.getenv("REFERRAL_BONUS", 50)) 
    DAILY_BONUS = int(os.getenv("DAILY_BONUS", 10)) 
    MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", 100)) 
    CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/your_channel_username") # Замените на реальную ссылку
    CHANNEL_ID = os.getenv("CHANNEL_ID") # ID канала для проверки подписки (например, -1001234567890)
    
    if not TOKEN:
        print("Критическая ошибка: TELEGRAM_BOT_TOKEN не найден ни в config.settings, ни в переменных окружения.")
        exit(1)
    if not ADMIN_IDS:
        print("Предупреждение: ADMIN_IDS не найдены. Функции администрирования могут быть недоступны.")
    if not CHANNEL_ID:
        print("Предупреждение: CHANNEL_ID не найден. Проверка подписки на канал может не работать корректно.")


from utils.database import Database
from utils.cron_server import CronServer # Убедитесь, что этот файл и класс существуют
from utils.helpers import format_currency
from models.user import User, WithdrawalRequest, Investment # Убедитесь, что модели корректны

# Настройка логгера
# logger = logging.getLogger(__name__) # Глобальный логгер, будет переопределен в BotLogger

# Импортируем обработчики - убедитесь, что пути и имена файлов/функций верны
from handlers.user import check_channel_subscription, show_channel_check, show_balance
from handlers.admin import handle_admin_command, handle_admin_message
from handlers.withdraw import (
    handle_withdraw_request,
    # notify_admins_withdrawal, # Если не используется, можно закомментировать или удалить
    handle_payment_details
)
from handlers.investments import show_investments, handle_investment_request
from handlers.referral import show_referral_program, handle_referral_bonus


class BotLogger:
    @staticmethod
    def setup_logging():
        # Устанавливаем базовую конфигурацию для корневого логгера
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO, # Уровень логирования по умолчанию
            handlers=[
                logging.FileHandler('bot.log', encoding='utf-8'), # Логи в файл
                logging.StreamHandler() # Логи в консоль
            ]
        )
        # Можно также настроить уровень логирования для конкретных библиотек, если они слишком "шумные"
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("telegram.ext").setLevel(logging.INFO)
        # Возвращаем логгер для основного приложения
        return logging.getLogger("TelegramBotApp")

class UserService:
    def __init__(self, database: Database):
        self.db = database
        self.logger = logging.getLogger(self.__class__.__name__) # Логгер для этого класса

    def is_admin(self, user_id: int) -> bool:
        return user_id in ADMIN_IDS

    def is_blocked(self, user_id: int) -> bool:
        user = self.db.get_user(user_id) # Предполагается, что get_user возвращает объект User или None
        return bool(user and user.is_blocked)

    async def create_user(self, user_id: int, ref_id: Optional[int] = None) -> User:
        try:
            user = self.db.create_user(user_id) # create_user должен возвращать созданный объект User
            if ref_id and ref_id != user_id:
                referrer = self.db.get_user(ref_id)
                # get_referral должен проверять, существует ли уже такая реферальная связь
                if referrer and not self.db.get_referral(referrer.id, user.id): # Используем PK пользователей
                    self.db.create_referral(referrer.id, user.id) # Используем PK
                    referrer.balance += REFERRAL_BONUS
                    referrer.total_earned += REFERRAL_BONUS
                    self.db.session.commit()
                    self.logger.info(f"User {user_id} (ID: {user.id}) joined via referral link from {ref_id} (ID: {referrer.id}). Bonus {REFERRAL_BONUS} to referrer.")
            return user
        except Exception as e:
            self.logger.error(f"Error creating user {user_id} with ref {ref_id}: {e}", exc_info=True)
            # В зависимости от критичности, можно либо пробросить исключение, либо вернуть None/спец.объект
            raise # Или return None, если создание пользователя не критично для продолжения

class BonusService:
    def __init__(self, database: Database):
        self.db = database
        self.logger = logging.getLogger(self.__class__.__name__)

    def can_claim_daily_bonus(self, user: User) -> tuple[bool, Optional[timedelta]]:
        if not user.last_bonus: # Если бонус никогда не брался
            return True, None
        now = datetime.now()
        time_since_last = now - user.last_bonus
        if time_since_last >= timedelta(days=1):
            return True, None
        next_bonus_time_exact = user.last_bonus + timedelta(days=1)
        time_left = next_bonus_time_exact - now
        return False, time_left

    def claim_daily_bonus(self, user: User) -> bool:
        try:
            can_claim, _ = self.can_claim_daily_bonus(user)
            if not can_claim:
                self.logger.info(f"User {user.user_id} tried to claim bonus too early.")
                return False
            user.balance += DAILY_BONUS
            user.total_earned += DAILY_BONUS
            user.last_bonus = datetime.now()
            # Здесь должна быть логика обновления user.bonus_streak, если она реализована
            self.db.session.commit()
            self.logger.info(f"Daily bonus {DAILY_BONUS} claimed by user {user.user_id}. New balance: {user.balance}")
            return True
        except Exception as e:
            self.logger.error(f"Error claiming daily bonus for user {user.user_id}: {e}", exc_info=True)
            self.db.session.rollback() # Откатываем изменения в случае ошибки
            return False

class WithdrawalService:
    def __init__(self, database: Database):
        self.db = database
        self.logger = logging.getLogger(self.__class__.__name__)

    def validate_withdrawal(self, user: User, amount: int) -> Dict[str, Any]:
        if amount < MIN_WITHDRAW:
            return {'valid': False, 'error': f'💰 Минимальная сумма вывода: {format_currency(MIN_WITHDRAW)}'}
        if amount > user.balance:
            return {'valid': False, 'error': f'💸 Недостаточно средств\n\nНеобходимо: {format_currency(amount)}\nДоступно: {format_currency(user.balance)}'}
        return {'valid': True, 'error': None} # Добавил error: None для консистентности

    def create_withdrawal_request(self, user_obj: User, amount: int, method: str, details: str) -> Optional[WithdrawalRequest]:
        try:
            # Важно: передаем объект User, а не telegram_id
            validation = self.validate_withdrawal(user_obj, amount)
            if not validation['valid']:
                self.logger.warning(f"Withdrawal validation failed for user {user_obj.user_id}: {validation.get('error')}")
                # Можно возвращать текст ошибки для пользователя
                # return validation.get('error')
                return None 

            # WithdrawalRequest.user_id должен быть FK на User.id (PK таблицы users)
            withdrawal = WithdrawalRequest(
                user_id=user_obj.id, 
                amount=amount,
                method=method,
                details=details,
                date=datetime.now(),
                status='pending'
            )
            user_obj.balance -= amount
            self.db.session.add(withdrawal)
            self.db.session.commit()
            self.logger.info(f"Withdrawal request created: user_id={user_obj.user_id} (User PK: {user_obj.id}), amount={amount}, method={method}")
            return withdrawal
        except Exception as e:
            self.logger.error(f"Error creating withdrawal request for user {user_obj.user_id}: {e}", exc_info=True)
            self.db.session.rollback()
            return None

    def process_withdrawal(self, withdrawal_id: int, approved: bool, admin_id: int) -> bool:
        try:
            withdrawal = self.db.session.query(WithdrawalRequest).get(withdrawal_id)
            if not withdrawal:
                self.logger.warning(f"Withdrawal request ID {withdrawal_id} not found for processing.")
                return False
            
            user_obj = withdrawal.user # Получаем связанный объект User (через backref/relationship)

            withdrawal.status = 'approved' if approved else 'rejected'
            withdrawal.processed_date = datetime.now()
            withdrawal.processed_by = admin_id # Предполагается, что это telegram_id админа

            if approved:
                user_obj.withdrawals += withdrawal.amount # У User должно быть поле withdrawals
                self.logger.info(f"Withdrawal ID {withdrawal_id} (User: {user_obj.user_id}) approved by admin {admin_id}.")
            else:
                user_obj.balance += withdrawal.amount 
                self.logger.info(f"Withdrawal ID {withdrawal_id} (User: {user_obj.user_id}) rejected by admin {admin_id}. Amount {withdrawal.amount} returned to user.")
            
            self.db.session.commit()
            return True
        except Exception as e:
            self.logger.error(f"Error processing withdrawal {withdrawal_id}: {e}", exc_info=True)
            self.db.session.rollback()
            return False

class MessageBuilder:
    @staticmethod
    def build_welcome_message(user: User, user_name: str) -> str:
        if user.total_earned >= 1000: status = "👑 VIP"
        elif user.total_earned >= 500: status = "🥇 Продвинутый"
        elif user.total_earned >= 100: status = "🥈 Активный"
        else: status = "🥉 Новичок"
        
        # Убедимся, что user.referrals существует и является списком/коллекцией
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
├ Текущий баланс: *{format_currency(user.balance)}*
├ Всего заработано: *{format_currency(user.total_earned)}*
├ Выведено средств: *{format_currency(user.withdrawals)}*
└ ROI: *{roi:.1f}%*

👥 *Партнёрская программа:*
├ Приглашено друзей: *{ref_count}*
├ Заработок с рефералов: *{format_currency(ref_earnings)}*
└ Средний доход с реферала: *{format_currency(ref_earnings / max(ref_count, 1))}*

📈 *Инвестиционная деятельность:*
├ Всего инвестировано: *{format_currency(total_invested)}*
├ Прибыль с инвестиций: *{format_currency(invest_earnings)}*
├ Активных планов: *{active_investments}*
└ Завершённых планов: *{len(investments) - active_investments}*

📅 *Активность:*
├ Дата регистрации: {user.join_date.strftime('%d.%m.%Y')}
└ Последний бонус: {user.last_bonus.strftime('%d.%m.%Y %H:%M') if user.last_bonus else 'Не получен'}"""

    @staticmethod
    def build_admin_panel_message(stats: Dict[str, Any]) -> str:
        # Данные должны быть уже подготовлены и переданы в stats
        return f"""👑 *ПАНЕЛЬ АДМИНИСТРАТОРА*

📊 *Статистика пользователей:*
├ Всего зарегистрировано: *{stats.get('total_users', 0):,}*
├ Активных за 24ч: *{stats.get('active_users_24h', 0):,}* 
├ Заблокированных: *{stats.get('blocked_users', 0):,}*
└ Новых за сегодня: *{stats.get('new_today', 0):,}*

💰 *Финансовая статистика:*
├ Общий баланс пользователей: *{format_currency(stats.get('total_balance_all_users', 0))}*
├ Всего выплачено: *{format_currency(stats.get('total_withdrawals_approved', 0))}*
└ Всего инвестировано: *{format_currency(stats.get('total_invested_all_users', 0))}*

⚙️ *Заявки:*
├ На вывод (в ожидании): *{stats.get('pending_withdrawals_count',0):,}*

🕐 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}"""

    @staticmethod
    def build_bonus_message(amount: int, balance: int, streak: int = 1) -> str:
        return f"""🎁 *ЕЖЕДНЕВНЫЙ БОНУС ПОЛУЧЕН!*

💰 Начислено: *{format_currency(amount)}*
💎 Новый баланс: *{format_currency(balance)}*
🔥 Серия дней: *{streak}* (Примечание: точный подсчет серии требует доработки)

⏰ Следующий бонус через 24 часа.
💡 Не пропускайте дни для увеличения серии!"""

    @staticmethod
    def build_info_message() -> str:
        # Убедитесь, что REFERRAL_BONUS, DAILY_BONUS, MIN_WITHDRAW, CHANNEL_LINK определены
        return f"""💡 *КАК ЗАРАБОТАТЬ В БОТЕ*

🚀 *Основные способы заработка:*

1️⃣ *Партнёрская программа*
├ Приглашайте друзей по реферальной ссылке.
├ Получайте *{format_currency(REFERRAL_BONUS)}* за каждого активного друга.
├ Друг должен подписаться на канал ({CHANNEL_LINK}) и проявить активность.
└ Неограниченное количество приглашений!

2️⃣ *Ежедневные бонусы*
├ Получайте *{format_currency(DAILY_BONUS)}* каждый день.
├ Бонус доступен каждые 24 часа.
├ (В разработке) Создавайте серии для дополнительных наград.

3️⃣ *Инвестиционные планы* (Примеры, настройте под себя)
├ 🌱 Стартер: от 100₽ • 1.2% в день
├ 💎 Стандарт: от 1,000₽ • 1.8% в день  
├ 🚀 Премиум: от 5,000₽ • 2.5% в день
└ 👑 VIP: от 20,000₽ • 3.5% в день

4️⃣ *Система достижений и статусов*
├ 🥉 Новичок: 0 - 99₽ заработано
├ 🥈 Активный: 100 - 499₽ заработано
├ 🥇 Продвинутый: 500 - 999₽ заработано
└ 👑 VIP: 1,000₽+ заработано

💸 *Вывод средств:*
├ Минимальная сумма: *{format_currency(MIN_WITHDRAW)}*
├ Доступные системы: Карта, QIWI, ЮMoney, Криптовалюта (USDT TRC20).
├ Обработка заявок: обычно в течение 24 часов.
└ Комиссия за вывод: 0% (мы покрываем расходы).

🎯 *Советы для максимального заработка:*
• Заходите каждый день за ежедневным бонусом.
• Активно приглашайте друзей по своей реферальной ссылке.
• Рассмотрите возможность инвестиций для пассивного дохода.
• Следите за новостями и акциями в нашем канале: {CHANNEL_LINK}"""

class KeyboardBuilder:
    @staticmethod
    def build_main_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
        keyboard_buttons = [
            [InlineKeyboardButton("💎 Баланс", callback_data='balance'), InlineKeyboardButton("📊 Статистика", callback_data='stats')],
            [InlineKeyboardButton("🚀 Инвестиции", callback_data='investments'), InlineKeyboardButton("👥 Партнёры", callback_data='referral')],
            [InlineKeyboardButton("💸 Вывод средств", callback_data='withdraw'), InlineKeyboardButton("🎁 Ежедневный бонус", callback_data='bonus')],
            [InlineKeyboardButton("🏆 Рейтинг", callback_data='top'), InlineKeyboardButton("💡 Как заработать", callback_data='info')],
            [InlineKeyboardButton("📢 Наш канал", url=CHANNEL_LINK), InlineKeyboardButton("📋 История", callback_data='history')]
        ]
        if is_admin:
            keyboard_buttons.append([InlineKeyboardButton("👑 АДМИН-ПАНЕЛЬ", callback_data='admin_panel')])
        return InlineKeyboardMarkup(keyboard_buttons)

    @staticmethod
    def build_admin_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Общая статистика", callback_data='admin_stats'), InlineKeyboardButton("👤 Упр. пользователями", callback_data='admin_users_manage')],
            [InlineKeyboardButton("📢 Массовая рассылка", callback_data='admin_broadcast'), InlineKeyboardButton("✉️ ЛС пользователю", callback_data='admin_send_to_user')],
            [InlineKeyboardButton("💰 Заявки на вывод", callback_data='admin_withdrawal_requests'), InlineKeyboardButton("📈 Упр. инвестициями", callback_data='admin_investments_manage')],
            [InlineKeyboardButton("🚫 Заблокировать", callback_data='admin_user_block'), InlineKeyboardButton("✅ Разблокировать", callback_data='admin_user_unblock')],
            [InlineKeyboardButton("⚙️ Настройки бота", callback_data='admin_bot_settings'), InlineKeyboardButton("🏠 Главное меню", callback_data='menu')]
        ]) # Изменил callback_data для большей ясности

    @staticmethod
    def build_payment_keyboard(amount: int) -> InlineKeyboardMarkup: # Для вывода средств
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Карта (RUB)", callback_data=f'req_withdraw_card_{amount}'), InlineKeyboardButton("🥝 QIWI (RUB)", callback_data=f'req_withdraw_qiwi_{amount}')],
            [InlineKeyboardButton("💛 ЮMoney (RUB)", callback_data=f'req_withdraw_ymoney_{amount}'), InlineKeyboardButton("₿ USDT (TRC20)", callback_data=f'req_withdraw_usdt_{amount}')],
            [InlineKeyboardButton("🔙 Назад (к сумме)", callback_data='withdraw')] # Возврат к выбору/вводу суммы
        ])

    @staticmethod
    def build_back_keyboard(callback_data: str = 'menu') -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В главное меню", callback_data=callback_data)]])

    @staticmethod
    def build_confirmation_keyboard(action_prefix: str, item_id: Any = "", extra_data: str = "") -> InlineKeyboardMarkup:
        # action_prefix: например, 'confirm_withdrawal_approve'
        # item_id: ID заявки, пользователя и т.д.
        # extra_data: дополнительные данные, если нужны
        confirm_cb = f"{action_prefix}_{item_id}"
        if extra_data:
            confirm_cb += f"_{extra_data}"
            
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить", callback_data=confirm_cb),
             InlineKeyboardButton("❌ Отмена", callback_data='admin_panel')] # или 'menu' в зависимости от контекста
        ])

class TelegramBot:
    def __init__(self):
        # Логгер инициализируется в main() до создания экземпляра TelegramBot,
        # поэтому здесь мы просто получаем его.
        self.logger = logging.getLogger("TelegramBotApp") 
        self.db = Database() 
        self.user_service = UserService(self.db)
        self.bonus_service = BonusService(self.db)
        self.withdrawal_service = WithdrawalService(self.db)
        self.logger.info("🚀 TelegramBot instance and its components initialized.")

    def setup_handlers(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("admin", handle_admin_command)) # handle_admin_command должен быть async
        application.add_handler(CallbackQueryHandler(self.button_handler))
        # handle_admin_message должен быть async и иметь логику для админских сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_message)) 
        self.logger.info("✅ Command and message handlers configured.")

    async def post_init(self, application: Application) -> None:
        try:
            self.db.init_db() # Создает таблицы, если их нет
            self.logger.info("✅ Database schema checked/initialized (post_init).")
            bot_info = await application.bot.get_me()
            self.logger.info(f"✅ Bot @{bot_info.username} is alive and running (post_init complete).")
        except Exception as e:
            self.logger.error(f"❌ Critical error during post_init: {e}", exc_info=True)
            # Здесь можно попытаться уведомить админов, если бот уже частично работает
            # Но если ошибка в БД, то многие функции могут быть недоступны
            raise # Важно пробросить ошибку, чтобы PTB корректно обработала сбой запуска

    async def cleanup(self, application: Application) -> None:
        try:
            if hasattr(self.db, 'session') and self.db.session and hasattr(self.db.session, 'close'):
                self.db.session.close()
                self.logger.info("✅ Database session closed successfully during cleanup.")
        except Exception as e:
            self.logger.error(f"❌ Error closing database session during cleanup: {e}", exc_info=True)
        self.logger.info("🧼 Bot cleanup process finished.")


    async def handle_daily_bonus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id = query.from_user.id
        self.logger.debug(f"User {user_id} attempting to claim daily bonus.")
        try:
            user = self.db.get_user(user_id)
            if not user:
                await query.edit_message_text("❌ Пользователь не найден. Пожалуйста, начните с /start.", parse_mode=ParseMode.MARKDOWN)
                return

            can_claim, time_left = self.bonus_service.can_claim_daily_bonus(user)
            if not can_claim:
                if time_left:
                    hours = int(time_left.total_seconds() // 3600)
                    minutes = int((time_left.total_seconds() % 3600) // 60)
                    await query.answer(f"⏳ Следующий бонус будет доступен через {hours}ч {minutes}мин.", show_alert=True)
                else: 
                    await query.answer("⏳ Бонус пока недоступен. Попробуйте позже.", show_alert=True)
                return

            if self.bonus_service.claim_daily_bonus(user):
                # Логика для streak (серии) пока что возвращает 1
                streak = self._calculate_bonus_streak(user) 
                bonus_text = MessageBuilder.build_bonus_message(DAILY_BONUS, user.balance, streak)
                await query.edit_message_text(bonus_text, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)
            else:
                await query.answer("❌ Не удалось начислить бонус. Возможно, вы уже получили его или произошла ошибка.", show_alert=True)
        except Exception as e:
            self.logger.error(f"Error in handle_daily_bonus for user {user_id}: {e}", exc_info=True)
            await self._send_error_message(update, "Произошла ошибка при получении ежедневного бонуса.")

    def _calculate_bonus_streak(self, user: User) -> int:
        # ЗАГЛУШКА. Для реальной серии дней:
        # 1. User должен иметь поле `bonus_streak` (int) и `last_bonus_claim_date` (date).
        # 2. При успешном взятии бонуса:
        #    Если `datetime.date.today() == user.last_bonus_claim_date + timedelta(days=1)`:
        #        `user.bonus_streak += 1`
        #    Если `datetime.date.today() > user.last_bonus_claim_date + timedelta(days=1)` (пропущен день):
        #        `user.bonus_streak = 1` (сброс серии)
        #    Иначе (первый бонус или в тот же день, что не должно быть возможно из-за can_claim_daily_bonus):
        #        `user.bonus_streak = 1`
        #    `user.last_bonus_claim_date = datetime.date.today()`
        #    `self.db.session.commit()`
        self.logger.debug(f"Calculating bonus streak for user {user.user_id} (current logic is a placeholder, returns 1).")
        return 1 

    async def show_admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id = query.from_user.id
        self.logger.debug(f"Admin panel requested by user {user_id}.")
        try:
            if not self.user_service.is_admin(user_id):
                await query.answer("❌ У вас нет прав для доступа к этой панели.", show_alert=True)
                return

            # Собираем статистику для админ-панели
            # Эти методы должны существовать в вашем self.db или быть реализованы здесь
            stats_data = {
                'total_users': self.db.session.query(User).count(),
                'active_users_24h': self.db.get_active_users_count_24h() if hasattr(self.db, 'get_active_users_count_24h') else 0,
                'blocked_users': self.db.session.query(User).filter_by(is_blocked=True).count(),
                'new_today': self.db.get_new_users_today_count() if hasattr(self.db, 'get_new_users_today_count') else 0,
                'total_balance_all_users': sum(u.balance for u in self.db.session.query(User.balance).all()) or 0,
                'total_withdrawals_approved': sum(w.amount for w in self.db.session.query(WithdrawalRequest.amount).filter_by(status='approved').all()) or 0,
                'total_invested_all_users': sum(u.total_invested for u in self.db.session.query(User.total_invested).all()) or 0,
                'pending_withdrawals_count': self.db.session.query(WithdrawalRequest).filter_by(status='pending').count()
            }
            
            stats_text = MessageBuilder.build_admin_panel_message(stats_data)
            await query.edit_message_text(stats_text, reply_markup=KeyboardBuilder.build_admin_keyboard(), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            self.logger.error(f"Error in show_admin_panel for user {user_id}: {e}", exc_info=True)
            await self._send_error_message(update, "Ошибка при загрузке админ-панели.")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        effective_user = update.effective_user
        if not effective_user: # Такого быть не должно, но на всякий случай
            self.logger.warning("Start command received without effective_user.")
            return

        user_id = effective_user.id
        self.logger.info(f"/start command initiated by user {user_id}.")
        try:
            if update.message and update.message.chat.type in ['group', 'supergroup']:
                await update.message.reply_text(f"ℹ️ Бот предназначен для личного использования. ID этого чата: `{update.message.chat.id}`", parse_mode=ParseMode.MARKDOWN)
                return

            user_name = effective_user.first_name or "Пользователь"
            ref_arg = context.args[0] if context.args and len(context.args) > 0 else None

            if self.user_service.is_blocked(user_id):
                blocked_text = "🚫 *ДОСТУП ОГРАНИЧЕН*\n\nВаш аккаунт заблокирован. Обратитесь в поддержку."
                if update.message: await update.message.reply_text(blocked_text, parse_mode=ParseMode.MARKDOWN)
                elif update.callback_query: await update.callback_query.message.reply_text(blocked_text, parse_mode=ParseMode.MARKDOWN)
                return

            user_obj = self.db.get_user(user_id) # Получаем объект User
            if not user_obj:
                ref_id_int = None
                if ref_arg and ref_arg.isdigit():
                    ref_id_int = int(ref_arg)
                    # Доп. проверка: не является ли реферер сам собой, и существует ли реферер
                    if ref_id_int == user_id: ref_id_int = None 
                    elif not self.db.get_user(ref_id_int): ref_id_int = None

                user_obj = await self.user_service.create_user(user_id, ref_id_int)
                self.logger.info(f"New user {user_id} (Obj ID: {user_obj.id}) created. Referrer ID: {ref_id_int if ref_id_int else 'None'}.")
            
            # Проверка подписки на канал (CHANNEL_ID должен быть задан в config/settings.py)
            if CHANNEL_ID and not self.user_service.is_admin(user_id):
                is_subscribed = await check_channel_subscription(context, user_id, CHANNEL_ID) # Передаем CHANNEL_ID
                if not is_subscribed:
                    await show_channel_check(update, context, CHANNEL_ID, CHANNEL_LINK) # Передаем ID и ссылку
                    return
            
            # Обновляем статус подписки, если пользователь подписался
            if CHANNEL_ID and hasattr(user_obj, 'channel_joined') and not user_obj.channel_joined:
                # Повторно проверяем на случай, если он только что подписался и нажал "Проверить"
                if await check_channel_subscription(context, user_id, CHANNEL_ID):
                    user_obj.channel_joined = True
                    self.db.session.commit()
                    self.logger.info(f"User {user_id} confirmed channel subscription (status updated).")

            welcome_text = MessageBuilder.build_welcome_message(user_obj, user_name)
            keyboard = KeyboardBuilder.build_main_keyboard(self.user_service.is_admin(user_id))

            if update.callback_query: # Если /start вызван из callback (например, кнопка "меню")
                await update.callback_query.edit_message_text(welcome_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            elif update.message:
                await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            self.logger.error(f"Error in /start command for user {user_id}: {e}", exc_info=True)
            await self._send_error_message(update, "Произошла непредвиденная ошибка при запуске бота.")

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data: # Доп. проверка
            self.logger.warning("Button_handler called with no query or query.data.")
            return
            
        await query.answer() 
        
        user_id = query.from_user.id
        self.logger.info(f"Button '{query.data}' pressed by user {user_id}.")

        try:
            if self.user_service.is_blocked(user_id):
                # Вместо reply_text, которое создаст новое сообщение, можно использовать answer с show_alert
                await query.answer("🚫 Вы заблокированы и не можете использовать эти функции.", show_alert=True)
                # Или, если нужно отредактировать текущее сообщение:
                # await query.edit_message_text("🚫 Вы заблокированы.", reply_markup=None)
                return

            user = self.db.get_user(user_id)
            if not user:
                await query.edit_message_text("❌ Пожалуйста, перезапустите бота командой /start.", 
                                              reply_markup=KeyboardBuilder.build_back_keyboard('menu'))
                return

            # Проверка подписки для всех действий, кроме check_subscription и если не админ
            if CHANNEL_ID and query.data != 'check_subscription' and not self.user_service.is_admin(user_id):
                if not await check_channel_subscription(context, user_id, CHANNEL_ID):
                    await show_channel_check(update, context, CHANNEL_ID, CHANNEL_LINK)
                    return
            
            await self._route_callback(update, context, query.data)
        except TelegramError as te:
            self.logger.error(f"Telegram API Error in button_handler (user {user_id}, data '{query.data}'): {te}", exc_info=True)
            # Сообщать пользователю об ошибках Telegram API может быть излишне, если они временные
            # await query.answer("Произошла ошибка связи с Telegram. Попробуйте еще раз.", show_alert=True)
        except Exception as e:
            self.logger.error(f"General Error in button_handler (user {user_id}, data '{query.data}'): {e}", exc_info=True)
            await self._send_error_message(update, "При обработке вашего запроса произошла ошибка.")

    async def _route_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
        user_id = update.effective_user.id
        is_admin = self.user_service.is_admin(user_id)

        # Основные маршруты
        simple_routes = {
            'balance': show_balance, 'stats': self._show_user_stats,
            'investments': show_investments, 'withdraw': handle_withdraw_request,
            'bonus': self.handle_daily_bonus, 'referral': show_referral_program,
            'top': self._show_top_users, 'info': self._show_info,
            'history': self._show_withdrawal_history, 'menu': self.start,
            'check_subscription': self._handle_check_subscription,
        }
        # Админские маршруты (простые)
        admin_simple_routes = {
            'admin_panel': self.show_admin_panel,
            'admin_stats': self._show_detailed_stats,
            # Добавьте другие простые админские команды, если они есть
        }

        if data in simple_routes:
            await simple_routes[data](update, context)
        elif data in admin_simple_routes:
            if is_admin:
                await admin_simple_routes[data](update, context)
            else:
                await update.callback_query.answer("❌ Доступ запрещен.", show_alert=True)
        # Сложные маршруты с параметрами
        elif data.startswith(('invest_', 'confirm_invest_', 'calc_')):
            await handle_investment_request(update, context) # Этот обработчик должен парсить data
        elif data.startswith('req_withdraw_'): # Изменил префикс для ясности (запрос деталей для вывода)
            parts = data.split('_') # req_withdraw_method_amount
            if len(parts) == 4 and parts[3].isdigit():
                method, amount_str = parts[2], parts[3]
                # Здесь должен быть вызов функции, которая запросит реквизиты
                # Например, await request_payment_details(update, context, method, int(amount_str))
                # Пока заглушка:
                await handle_payment_details(update, context, payment_method=method, amount=int(amount_str))
                self.logger.info(f"User {user_id} selected withdrawal method {method} for amount {amount_str}")
            else:
                self.logger.warning(f"Invalid withdrawal request callback: {data} from user {user_id}")
                await self._handle_unknown_callback(update, context)
        
        # Обработка callback'ов подтверждения (пример)
        elif data.startswith('confirm_withdrawal_approve_'):
            if is_admin:
                withdrawal_id_str = data.replace('confirm_withdrawal_approve_', '')
                if withdrawal_id_str.isdigit():
                    # await self.withdrawal_service.process_withdrawal(int(withdrawal_id_str), True, user_id)
                    # И отправить уведомление пользователю и обновить сообщение админа
                    await update.callback_query.answer(f"Обработка одобрения заявки {withdrawal_id_str}...", show_alert=False)
                    self.logger.info(f"Admin {user_id} confirmed approval for withdrawal {withdrawal_id_str} (placeholder).")
                else: await self._handle_unknown_callback(update, context)
            else: await update.callback_query.answer("❌ Доступ запрещен.", show_alert=True)

        # Другие админские команды (из build_admin_keyboard)
        elif data in ['admin_users_manage', 'admin_broadcast', 'admin_send_to_user', 
                      'admin_withdrawal_requests', 'admin_investments_manage', 
                      'admin_user_block', 'admin_user_unblock', 'admin_bot_settings'] and is_admin:
            # Здесь должны быть вызовы соответствующих админских функций
            # Например, await handle_admin_user_management(update, context, action=data)
            self.logger.info(f"Admin action '{data}' called by admin {user_id} (implementation pending).")
            await update.callback_query.answer(f"Админ-действие '{data}' находится в разработке.", show_alert=True)
        
        else:
            await self._handle_unknown_callback(update, context)

    async def _handle_check_subscription(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        self.logger.debug(f"User {user_id} initiated subscription check.")
        if not CHANNEL_ID:
            await update.callback_query.answer("ID канала для проверки не настроен.", show_alert=True)
            await self.start(update, context) # Вернуть в меню, т.к. проверка невозможна
            return

        if await check_channel_subscription(context, user_id, CHANNEL_ID):
            user = self.db.get_user(user_id)
            if user and hasattr(user, 'channel_joined') and not user.channel_joined:
                user.channel_joined = True
                self.db.session.commit()
                self.logger.info(f"User {user_id} subscription confirmed and status updated in DB.")
            await update.callback_query.answer("✅ Спасибо за подписку! Доступ открыт.", show_alert=False)
            await self.start(update, context) 
        else:
            await update.callback_query.answer(f"Вы все еще не подписаны на канал {CHANNEL_LINK}. Пожалуйста, подпишитесь и нажмите кнопку еще раз.", show_alert=True)
            # Можно не показывать снова show_channel_check, т.к. пользователь уже видит это сообщение

    async def _show_user_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id = query.from_user.id
        self.logger.debug(f"User stats requested by user {user_id}.")
        user = self.db.get_user(user_id)
        if not user:
            await query.edit_message_text("❌ Пользователь не найден. Попробуйте /start.", parse_mode=ParseMode.MARKDOWN)
            return
        stats_text = MessageBuilder.build_stats_message(user)
        await query.edit_message_text(stats_text, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)

    async def _show_detailed_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Этот метод вызывается из админ-панели, поэтому query должен быть
        query = update.callback_query 
        user_id = query.from_user.id
        self.logger.debug(f"Detailed system stats requested by admin {user_id}.")
        try:
            # Используем данные, собранные в show_admin_panel, если они там уже есть,
            # или собираем их заново здесь. Для простоты, можно дублировать логику сбора.
            # Важно: эти запросы могут быть тяжелыми на больших базах. Кэширование или оптимизация могут потребоваться.
            
            # Данные о пользователях
            total_users_count = self.db.session.query(User).count()
            active_24h = self.db.get_active_users_count_24h() if hasattr(self.db, 'get_active_users_count_24h') else "N/A"
            blocked_count = self.db.session.query(User).filter_by(is_blocked=True).count()
            new_today_count = self.db.get_new_users_today_count() if hasattr(self.db, 'get_new_users_today_count') else "N/A"
            subscribed_count = self.db.get_subscribed_users_count() if hasattr(self.db, 'get_subscribed_users_count') else "N/A"

            # Финансовые данные
            total_balance = sum(u.balance for u in self.db.session.query(User.balance).all()) or 0
            total_withdrawn = sum(w.amount for w in self.db.session.query(WithdrawalRequest.amount).filter_by(status='approved').all()) or 0
            total_invested_val = sum(u.total_invested for u in self.db.session.query(User.total_invested).all()) or 0
            
            # Данные по заявкам и рефералам
            pending_withdrawals = self.db.session.query(WithdrawalRequest).filter_by(status='pending').count()
            # total_referrals = self.db.get_total_referrals_count() # если есть такой метод
            # avg_earnings = self.db.get_average_earnings_per_user() # если есть

            stats_text = f"""📊 *ПОДРОБНАЯ СТАТИСТИКА СИСТЕМЫ*

👥 *Пользователи:*
├ Всего: *{total_users_count:,}*
├ Активных за 24ч: *{active_24h if isinstance(active_24h, str) else f'{active_24h:,}'}*
├ Заблокировано: *{blocked_count:,}*
├ Новых сегодня: *{new_today_count if isinstance(new_today_count, str) else f'{new_today_count:,}'}*
└ Подписано на канал: *{subscribed_count if isinstance(subscribed_count, str) else f'{subscribed_count:,}'}*

💰 *Финансы:*
├ Общий баланс всех: *{format_currency(total_balance)}*
├ Всего выплачено (одобрено): *{format_currency(total_withdrawn)}*
└ Всего инвестировано: *{format_currency(total_invested_val)}*

📈 *Активность:*
├ Заявок на вывод (в ожидании): *{pending_withdrawals:,}*
{f'├ Всего реферальных связей: *{self.db.get_total_referrals_count():,}*' if hasattr(self.db, 'get_total_referrals_count') else ""}
{f'└ Средний заработок на пользователя: *{format_currency(self.db.get_average_earnings_per_user())}*' if hasattr(self.db, 'get_average_earnings_per_user') else ""}

🕐 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"""
            await query.edit_message_text(stats_text, reply_markup=KeyboardBuilder.build_back_keyboard('admin_panel'), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            self.logger.error(f"Error showing detailed stats for admin {user_id}: {e}", exc_info=True)
            await self._send_error_message(update, "Ошибка при загрузке детальной статистики.")


    async def _show_top_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id = query.from_user.id
        self.logger.debug(f"Top users list requested by user {user_id}.")
        try:
            top_users_db = self.db.session.query(User).order_by(User.total_earned.desc(), User.balance.desc()).limit(10).all()
            top_text = "🏆 *ТОП-10 УСПЕШНЫХ ПОЛЬЗОВАТЕЛЕЙ*\n\n"
            medals = ["🥇", "🥈", "🥉"] + [f"{i}️⃣" for i in range(4, 11)]

            if not top_users_db:
                top_text += "Пока нет пользователей в рейтинге. Станьте первым!"
            else:
                for i, user_db_obj in enumerate(top_users_db):
                    name_display = f"ID {user_db_obj.user_id}" # По умолчанию
                    try:
                        # Пытаемся получить имя пользователя, но не падаем, если не удается
                        chat_info = await context.bot.get_chat(user_db_obj.user_id)
                        temp_name = chat_info.first_name or chat_info.username
                        if temp_name: name_display = temp_name
                        name_display = name_display[:20].rstrip() + ("..." if len(name_display) > 20 else "")
                    except TelegramError as te: 
                        self.logger.warning(f"TelegramError getting chat info for top user {user_db_obj.user_id}: {te.message}")
                    except Exception as e_chat: 
                        self.logger.error(f"Unexpected error getting chat info for top user {user_db_obj.user_id}: {e_chat}", exc_info=False) # exc_info=False чтобы не засорять логи, если это частая проблема
                    
                    refs_count = len(user_db_obj.referrals) if hasattr(user_db_obj, 'referrals') else 0
                    active_inv_count = 0
                    if hasattr(user_db_obj, 'investments'):
                        active_inv_count = len([inv for inv in user_db_obj.investments if hasattr(inv, 'is_finished') and not inv.is_finished])
                    
                    top_text += f"{medals[i]} *{name_display}*\n"
                    top_text += f"├ Заработано: *{format_currency(user_db_obj.total_earned)}*\n"
                    top_text += f"├ Рефералов: *{refs_count}*\n"
                    top_text += f"└ Активных инвестиций: *{active_inv_count}*\n\n"
            
            top_text += "\n💡 *Станьте частью топа! Приглашайте друзей и инвестируйте.*"
            await query.edit_message_text(top_text, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            self.logger.error(f"Error in _show_top_users for user {user_id}: {e}", exc_info=True)
            await self._send_error_message(update, "Ошибка при загрузке рейтинга пользователей.")

    async def _show_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id = query.from_user.id
        self.logger.debug(f"Info section requested by user {user_id}.")
        info_text = MessageBuilder.build_info_message()
        await query.edit_message_text(info_text, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)

    async def _show_withdrawal_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id_tg = query.from_user.id # Telegram ID
        self.logger.debug(f"Withdrawal history requested by user {user_id_tg}.")
        try:
            user_obj = self.db.get_user(user_id_tg) # Получаем объект User по Telegram ID
            if not user_obj:
                await query.edit_message_text("❌ Пользователь не найден. Попробуйте /start.", parse_mode=ParseMode.MARKDOWN)
                return

            # WithdrawalRequest.user_id должен быть FK на User.id (первичный ключ таблицы users)
            withdrawals_list = (
                self.db.session.query(WithdrawalRequest)
                .filter_by(user_id=user_obj.id) 
                .order_by(WithdrawalRequest.date.desc())
                .limit(10) # Ограничиваем количество для отображения
                .all()
            )
            
            if not withdrawals_list:
                history_text = f"📋 *ИСТОРИЯ ВЫВОДОВ*\n\n❌ У вас пока нет заявок на вывод средств.\n\n💡 Минимальная сумма для вывода: {format_currency(MIN_WITHDRAW)}"
            else:
                history_text = "📋 *ИСТОРИЯ ВЫВОДОВ* (последние {}):\n\n".format(len(withdrawals_list))
                total_requested_shown = sum(w.amount for w in withdrawals_list)
                approved_shown_count = len([w for w in withdrawals_list if w.status == 'approved'])
                
                history_text += f"📊 *Показано заявок:* {len(withdrawals_list)}\n"
                history_text += f"├ Одобрено из показанных: *{approved_shown_count}*\n"
                history_text += f"└ Сумма показанных заявок: *{format_currency(total_requested_shown)}*\n\n"
                
                for w_req in withdrawals_list:
                    status_emoji = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}.get(w_req.status, '❓')
                    status_text_map = {'pending': 'В обработке', 'approved': 'Одобрена', 'rejected': 'Отклонена'}
                    status_display = status_text_map.get(w_req.status, 'Неизвестный статус')
                    
                    history_text += f"🆔 *Заявка #{w_req.id}* | Сумма: *{format_currency(w_req.amount)}*\n"
                    history_text += f"├ Система: *{w_req.method.upper()}* | Дата: {w_req.date.strftime('%d.%m.%y %H:%M')}\n"
                    history_text += f"└ Статус: {status_emoji} {status_display}\n\n"

            await query.edit_message_text(history_text, reply_markup=KeyboardBuilder.build_back_keyboard('menu'), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            self.logger.error(f"Error in _show_withdrawal_history for user {user_id_tg}: {e}", exc_info=True)
            await self._send_error_message(update, "Произошла ошибка при загрузке истории выводов.")

    async def _handle_unknown_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        self.logger.warning(f"Unknown callback_data '{query.data}' received from user {query.from_user.id}.")
        await query.answer("❓ Неизвестная команда или действие было отменено.", show_alert=True)
        # Можно добавить возврат в главное меню, если это уместно
        # await self.start(update, context) 

    async def _send_error_message(self, update: Update, error_text_user: str) -> None:
        # error_text_user - текст для пользователя
        internal_error_guid = os.urandom(4).hex() # Генерируем короткий ID для логов
        self.logger.error(f"Error occurred (GUID: {internal_error_guid}). User message: '{error_text_user}'")
        
        user_message = f"❌ {error_text_user}\n\n" \
                       f"Пожалуйста, попробуйте еще раз. Если ошибка повторяется, " \
                       f"обратитесь в службу поддержки, указав код ошибки: `{internal_error_guid}`"
        
        keyboard = KeyboardBuilder.build_back_keyboard('menu')
        try:
            if update.callback_query and update.callback_query.message:
                # Пытаемся отредактировать сообщение, если это callback
                await update.callback_query.edit_message_text(user_message, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            elif update.message:
                # Отправляем новое сообщение, если это была команда
                await update.message.reply_text(user_message, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            # Если это callback, но сообщение не может быть отредактировано (например, слишком старое)
            elif update.callback_query:
                 await update.callback_query.answer(error_text_user, show_alert=True)
                 # И дополнительно отправить новое сообщение, если нужно
                 await context.bot.send_message(chat_id=update.effective_chat.id, text=user_message, 
                                                reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        except TelegramError as te: 
            self.logger.error(f"TelegramError while sending error message to user (GUID: {internal_error_guid}): {te.message}")
        except Exception as e_send: 
            self.logger.error(f"Unexpected error while sending error message to user (GUID: {internal_error_guid}): {e_send}", exc_info=True)


async def main():
    # Инициализация логгирования в самом начале
    app_logger = BotLogger.setup_logging()
    
    application_ptb = None # Для доступа в finally
    cron_server_instance = None # Для доступа в finally
    telegram_bot_service_instance = None # Для доступа в finally

    try:
        app_logger.info("🏁 Application starting...")
        
        # Создаем экземпляр нашего класса TelegramBot, который содержит всю логику
        telegram_bot_service_instance = TelegramBot() 
        
        if not TOKEN: 
            app_logger.critical("❌ FATAL: TELEGRAM_BOT_TOKEN is not defined. Exiting application.")
            return # Завершаем работу, если токена нет

        # Создаем экземпляр Application из python-telegram-bot
        application_ptb = Application.builder().token(TOKEN).build()
        
        # Настраиваем обработчики команд и сообщений
        telegram_bot_service_instance.setup_handlers(application_ptb)
        
        # Настраиваем хуки жизненного цикла PTB (post_init, post_shutdown)
        application_ptb.post_init = telegram_bot_service_instance.post_init
        application_ptb.post_shutdown = telegram_bot_service_instance.cleanup
        
        render_app_url = os.getenv('RENDER_EXTERNAL_URL') # URL вашего приложения на Render
        
        # Определяем режим работы: Webhook (для Render) или Polling (для локальной разработки)
        if render_app_url and os.getenv('RENDER'): # RENDER - это стандартная переменная окружения на Render
            app_logger.info("📡 Starting in Webhook mode (detected Render environment).")
            
            port_env_str = os.getenv('PORT') # Render предоставляет порт через переменную PORT
            if not port_env_str or not port_env_str.isdigit():
                app_logger.critical(f"❌ FATAL: Environment variable PORT is not set or invalid ('{port_env_str}'). Exiting.")
                return
            listen_port = int(port_env_str)

            # Путь для вебхука лучше сделать секретным, например, сам токен
            webhook_path_segment = TOKEN 
            full_webhook_url_for_telegram = f"{render_app_url.rstrip('/')}/{webhook_path_segment}"
            
            app_logger.info(f"Configuring Webhook: URL for Telegram API -> {full_webhook_url_for_telegram}")
            app_logger.info(f"Webhook server will listen on -> 0.0.0.0:{listen_port}, Path -> /{webhook_path_segment}")
            
            # Запуск Cron сервера, если он есть и настроен
            if render_app_url and CronServer and hasattr(CronServer, 'start'): 
                try:
                    cron_server_instance = CronServer(render_app_url) # Передаем URL для возможных пингов
                    await cron_server_instance.start() 
                    app_logger.info("⏰ Cron server started successfully.")
                except Exception as e_cron:
                    app_logger.warning(f"⚠️ Failed to start cron server: {e_cron}", exc_info=True)
            
            # Запускаем PTB в режиме webhook. Это блокирующий вызов.
            await application_ptb.run_webhook(
                listen="0.0.0.0", # Слушаем на всех интерфейсах
                port=listen_port,
                url_path=webhook_path_segment, # Локальный путь, который слушает наш сервер
                webhook_url=full_webhook_url_for_telegram, # URL, который регистрируется в Telegram API
                drop_pending_updates=True # Удаляем старые обновления при старте
                # secret_token="YOUR_SECRET_PHRASE" # Опционально: для дополнительной верификации запросов от Telegram
            )
            # Код здесь не выполнится, пока run_webhook работает
            
        else:
            # Режим Polling для локальной разработки
            app_logger.info("🔄 Starting in Polling mode (local development or non-Render environment).")
            
            # Перед запуском polling, удаляем любой существующий вебхук
            app_logger.info("Attempting to delete any existing webhook settings...")
            await application_ptb.bot.delete_webhook(drop_pending_updates=True)
            app_logger.info("Webhook (if any) successfully deleted.")

            # Запуск Cron сервера (если нужен локально и есть URL для пинга, например, ngrok)
            if render_app_url and CronServer and hasattr(CronServer, 'start'): # render_app_url может быть ngrok URL
                 try:
                    cron_server_instance = CronServer(render_app_url)
                    await cron_server_instance.start()
                    app_logger.info("⏰ Cron server started (local).")
                 except Exception as e_cron_local:
                    app_logger.warning(f"⚠️ Failed to start cron server (local): {e_cron_local}", exc_info=True)
            
            # Запускаем PTB в режиме polling. Это блокирующий вызов.
            app_logger.info("Starting polling for updates from Telegram...")
            await application_ptb.run_polling(
                drop_pending_updates=True,
                poll_interval=0.5, # Как часто проверять обновления (сек)
                timeout=10         # Таймаут для одного запроса getUpdates (сек)
            )
            # Код здесь не выполнится, пока run_polling работает
        
    except KeyboardInterrupt:
        app_logger.info("🛑 Bot stopped by user (KeyboardInterrupt). Performing cleanup...")
    except SystemExit as se: # Для обработки exit(1) и т.п.
        app_logger.info(f"Application exited with code {se.code}.")
    except Exception as e_main:
        app_logger.critical(f"💥 CRITICAL UNHANDLED ERROR in main application loop: {e_main}", exc_info=True)
        # Попытка уведомить администраторов о критической ошибке
        if application_ptb and telegram_bot_service_instance and ADMIN_IDS:
            error_report_message = f"🚨 *КРИТИЧЕСКАЯ ОШИБКА БОТА*\n\nБот остановлен из-за непредвиденной ошибки:\n`{type(e_main).__name__}: {str(e_main)}`\n\nПроверьте логи сервера для получения подробной информации."
            for admin_tg_id in ADMIN_IDS:
                try:
                    await application_ptb.bot.send_message(chat_id=admin_tg_id, text=error_report_message, parse_mode=ParseMode.MARKDOWN)
                except Exception as e_send_admin_err:
                    app_logger.error(f"Failed to send critical error notification to admin {admin_tg_id}: {e_send_admin_err}")
    finally:
        app_logger.info("🧼 Initiating final cleanup (if any)...")
        if cron_server_instance and hasattr(cron_server_instance, 'stop') and callable(cron_server_instance.stop):
            app_logger.info("Stopping cron server...")
            try:
                # Проверяем, является ли метод stop асинхронным
                if asyncio.iscoroutinefunction(cron_server_instance.stop):
                    await cron_server_instance.stop()
                else:
                    cron_server_instance.stop()
                app_logger.info("Cron server stopped.")
            except Exception as e_cron_stop_final:
                app_logger.error(f"Error stopping cron server during final cleanup: {e_cron_stop_final}", exc_info=True)
        
        # PTB v20+ автоматически обрабатывает остановку application при завершении run_polling/run_webhook
        # Явный вызов application.stop() обычно не требуется и может вызвать проблемы, если сделан неверно.
        
        app_logger.info("🚪 Application shutdown process finished.")


if __name__ == '__main__':
    # Этот блок выполняется только при прямом запуске файла (python bot.py)
    
    # Загрузка переменных окружения из .env файла, если он есть (для локальной разработки)
    # Установите python-dotenv: pip install python-dotenv
    try:
        from dotenv import load_dotenv
        if load_dotenv():
             print("Loaded environment variables from .env file.")
        else:
             print("No .env file found or it is empty. Using system environment variables.")
    except ImportError:
        print("python-dotenv library not found. Skipping .env file loading. Ensure environment variables are set.")

    # Переопределение констант из config.settings переменными окружения, если они установлены
    # Это позволяет легко менять настройки для разных сред (dev, prod) без изменения кода
    # TOKEN, ADMIN_IDS и т.д. уже должны быть либо из config.settings, либо из os.getenv в начале файла.
    # Здесь можно добавить дополнительные проверки или логирование загруженных настроек.
    
    # Проверка наличия TOKEN перед запуском (важнейшая переменная)
    # TOKEN загружается в начале файла, поэтому здесь он уже должен быть определен.
    if not TOKEN: # Эта проверка дублируется с той, что в начале файла, но для __main__ она важна.
        print("FATAL ERROR: TELEGRAM_BOT_TOKEN is not set. Cannot start the bot.")
        print("Please set it in your config/settings.py, .env file, or as an environment variable.")
        exit(1) # Завершаем программу, если нет токена
    
    # Запускаем асинхронную функцию main
    asyncio.run(main())
