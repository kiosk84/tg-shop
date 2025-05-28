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
from config.settings import *
from utils.database import Database
from utils.cron_server import CronServer
from utils.helpers import format_currency
from models.user import User, WithdrawalRequest, Investment

# Настройка логгера
logger = logging.getLogger(__name__)

# Импортируем обработчики
from handlers.user import check_channel_subscription, show_channel_check, show_balance
from handlers.admin import handle_admin_command, handle_admin_message
from handlers.withdraw import (
    handle_withdraw_request, 
    notify_admins_withdrawal, 
    handle_payment_details
)
from handlers.investments import show_investments, handle_investment_request
from handlers.referral import show_referral_program, handle_referral_bonus

class BotLogger:
    """Настройка логирования для бота"""
    
    @staticmethod
    def setup_logging():
        """Настройка системы логирования"""
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO,
            handlers=[
                logging.FileHandler('bot.log', encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        return logging.getLogger(__name__)

class UserService:
    """Сервис для работы с пользователями"""
    
    def __init__(self, database: Database):
        self.db = database
        self.logger = logging.getLogger(__name__)
    
    def is_admin(self, user_id: int) -> bool:
        """Проверка является ли пользователь админом"""
        return user_id in ADMIN_IDS
    
    def is_blocked(self, user_id: int) -> bool:
        """Проверка заблокирован ли пользователь"""
        user = self.db.get_user(user_id)
        return user and user.is_blocked if user else False
    
    async def create_user(self, user_id: int, ref_id: Optional[int] = None) -> User:
        """Создание нового пользователя с реферальной системой"""
        try:
            # Создаем нового пользователя
            user = self.db.create_user(user_id)
            
            # Обработка реферальной ссылки
            if ref_id and ref_id != user_id:
                referrer = self.db.get_user(ref_id)
                if referrer and not self.db.get_referral(ref_id, user_id):
                    # Создаем реферальную связь
                    self.db.create_referral(ref_id, user_id)
                    # Начисляем бонус рефереру
                    referrer.balance += REFERRAL_BONUS
                    referrer.total_earned += REFERRAL_BONUS
                    self.db.session.commit()
                    self.logger.info(f"User {user_id} joined via referral link {ref_id}")
            
            return user
            
        except Exception as e:
            self.logger.error(f"Error creating user {user_id}: {e}")
            raise

class BonusService:
    """Сервис для работы с бонусами"""
    
    def __init__(self, database: Database):
        self.db = database
        self.logger = logging.getLogger(__name__)
    
    def can_claim_daily_bonus(self, user: User) -> tuple[bool, Optional[timedelta]]:
        """Проверка возможности получения ежедневного бонуса"""
        now = datetime.now()
        time_since_last = now - user.last_bonus
        
        if time_since_last >= timedelta(days=1):
            return True, None
        
        next_bonus_time = user.last_bonus + timedelta(days=1) - now
        return False, next_bonus_time
    
    def claim_daily_bonus(self, user: User) -> bool:
        """Начисление ежедневного бонуса"""
        try:
            can_claim, _ = self.can_claim_daily_bonus(user)
            if not can_claim:
                return False
            
            user.balance += DAILY_BONUS
            user.total_earned += DAILY_BONUS
            user.last_bonus = datetime.now()
            self.db.session.commit()
            
            self.logger.info(f"Daily bonus claimed by user {user.user_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error claiming daily bonus for user {user.user_id}: {e}")
            return False

class WithdrawalService:
    """Сервис для работы с выводом средств"""
    
    def __init__(self, database: Database):
        self.db = database
        self.logger = logging.getLogger(__name__)
    
    def validate_withdrawal(self, user: User, amount: int) -> Dict[str, Any]:
        """Валидация запроса на вывод"""
        if amount < MIN_WITHDRAW:
            return {
                'valid': False,
                'error': f'💰 Минимальная сумма вывода: {MIN_WITHDRAW:,}₽'
            }
        
        if amount > user.balance:
            return {
                'valid': False,
                'error': f'💸 Недостаточно средств\n\nНеобходимо: {amount:,}₽\nДоступно: {user.balance:,}₽'
            }
        
        return {'valid': True}
    
    def create_withdrawal_request(self, user: User, amount: int, method: str, details: str) -> Optional[WithdrawalRequest]:
        """Создание заявки на вывод"""
        try:
            validation = self.validate_withdrawal(user, amount)
            if not validation['valid']:
                return None
            
            # Создаем заявку
            withdrawal = WithdrawalRequest(
                user_id=user.id,
                amount=amount,
                method=method,
                details=details,
                date=datetime.now(),
                status='pending'
            )
            
            # Списываем средства с баланса
            user.balance -= amount
            
            self.db.session.add(withdrawal)
            self.db.session.commit()
            
            self.logger.info(f"Withdrawal request created: user_id={user.user_id}, amount={amount}")
            return withdrawal
        except Exception as e:
            self.logger.error(f"Error creating withdrawal request: {e}")
            self.db.session.rollback()
            return None
    
    def process_withdrawal(self, withdrawal_id: int, approved: bool, admin_id: int) -> bool:
        """Обработка заявки на вывод"""
        try:
            withdrawal = self.db.session.query(WithdrawalRequest).get(withdrawal_id)
            if not withdrawal:
                return False
            
            withdrawal.status = 'approved' if approved else 'rejected'
            withdrawal.processed_date = datetime.now()
            withdrawal.processed_by = admin_id
            
            if approved:
                withdrawal.user.withdrawals += withdrawal.amount
            else:
                # Возвращаем средства на баланс
                withdrawal.user.balance += withdrawal.amount
            
            self.db.session.commit()
            self.logger.info(f"Withdrawal {withdrawal_id} {'approved' if approved else 'rejected'}")
            return True
        except Exception as e:
            self.logger.error(f"Error processing withdrawal {withdrawal_id}: {e}")
            self.db.session.rollback()
            return False

class MessageBuilder:
    """Строитель сообщений для бота"""
    
    @staticmethod
    def build_welcome_message(user: User, user_name: str) -> str:
        """Построить приветственное сообщение"""
        # Определяем статус пользователя
        if user.total_earned >= 1000:
            status = "👑 VIP"
        elif user.total_earned >= 500:
            status = "🥇 Продвинутый"
        elif user.total_earned >= 100:
            status = "🥈 Активный"
        else:
            status = "🥉 Новичок"
        
        return f"""🚀 *Добро пожаловать, {user_name}!*

{status} • ID: `{user.user_id}`

💎 *Ваш профиль:*
├ Баланс: *{format_currency(user.balance)}*
├ Заработано: *{format_currency(user.total_earned)}*
├ Выведено: *{format_currency(user.withdrawals)}*
└ Рефералов: *{len(user.referrals)}*

🎯 Выберите действие для продолжения:"""
    
    @staticmethod
    def build_stats_message(user: User) -> str:
        """Построить сообщение статистики"""
        ref_count = len(user.referrals)
        ref_earnings = sum(r.bonus_paid for r in user.referrals)
        invest_earnings = sum(i.total_profit for i in user.investments)
        active_investments = len([i for i in user.investments if not i.is_finished])
        
        # Расчет ROI
        roi = (user.total_earned / max(user.total_invested, 1)) * 100 if user.total_invested > 0 else 0
        
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
├ Всего инвестировано: *{format_currency(user.total_invested)}*
├ Прибыль с инвестиций: *{format_currency(invest_earnings)}*
├ Активных планов: *{active_investments}*
└ Завершённых планов: *{len(user.investments) - active_investments}*

📅 *Активность:*
├ Дата регистрации: {user.join_date.strftime('%d.%m.%Y')}
└ Последний бонус: {user.last_bonus.strftime('%d.%m.%Y') if user.last_bonus else 'Не получен'}"""
    
    @staticmethod
    def build_admin_panel_message(stats: Dict[str, Any]) -> str:
        """Построить сообщение админ-панели"""
        return f"""👑 *ПАНЕЛЬ АДМИНИСТРАТОРА*

📊 *Статистика пользователей:*
├ Всего зарегистрировано: *{stats['total_users']:,}*
├ Активных пользователей: *{stats['active_users']:,}*
├ Заблокированных: *{stats['blocked_users']:,}*
└ Новых за сегодня: *{stats.get('new_today', 0):,}*

💰 *Финансовая статистика:*
├ Общий баланс: *{format_currency(stats.get('total_balance', 0))}*
├ Выплачено: *{format_currency(stats.get('total_withdrawals', 0))}*
└ Инвестировано: *{format_currency(stats.get('total_investments', 0))}*

🕐 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}"""
    
    @staticmethod
    def build_bonus_message(amount: int, balance: int, streak: int = 1) -> str:
        """Построить сообщение о получении бонуса"""
        return f"""🎁 *ЕЖЕДНЕВНЫЙ БОНУС ПОЛУЧЕН!*

💰 Начислено: *{format_currency(amount)}*
💎 Новый баланс: *{format_currency(balance)}*
🔥 Серия дней: *{streak}*

⏰ Следующий бонус через 24 часа
💡 Не пропускайте дни для увеличения серии!"""
    
    @staticmethod
    def build_info_message() -> str:
        """Построить информационное сообщение"""
        return f"""💡 *КАК ЗАРАБОТАТЬ В БОТЕ*

🚀 *Основные способы заработка:*

1️⃣ *Партнёрская программа*
├ Приглашайте друзей по реферальной ссылке
├ Получайте {REFERRAL_BONUS:,}₽ за каждого активного друга
├ Друг должен подписаться на канал и быть активным
└ Неограниченное количество приглашений

2️⃣ *Ежедневные бонусы*
├ Получайте {DAILY_BONUS:,}₽ каждый день
├ Бонус доступен каждые 24 часа
├ Создавайте серии для дополнительных наград
└ Максимальная серия увеличивает бонус

3️⃣ *Инвестиционные планы*
├ 🌱 Стартер: от 100₽ • 1.2% в день
├ 💎 Стандарт: от 1,000₽ • 1.8% в день  
├ 🚀 Премиум: от 5,000₽ • 2.5% в день
└ 👑 VIP: от 20,000₽ • 3.5% в день

4️⃣ *Система достижений*
├ 🥉 Новичок: 0-99₽ заработано
├ 🥈 Активный: 100-499₽ заработано
├ 🥇 Продвинутый: 500-999₽ заработано
└ 👑 VIP: 1,000₽+ заработано

💸 *Вывод средств:*
├ Минимальная сумма: {MIN_WITHDRAW:,}₽
├ Доступные системы: Карта, QIWI, ЮMoney, Крипта
├ Обработка заявок: до 24 часов
└ Комиссия: 0% (мы платим за вас!)

🎯 *Советы для максимального заработка:*
• Заходите каждый день за бонусом
• Приглашайте активных друзей
• Инвестируйте для пассивного дохода
• Следите за новостями в канале"""

class KeyboardBuilder:
    """Строитель клавиатур для бота"""
    
    @staticmethod
    def build_main_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
        """Построить главную клавиатуру с улучшенным дизайном"""
        keyboard = [
            [
                InlineKeyboardButton("💎 Баланс", callback_data='balance'),
                InlineKeyboardButton("📊 Статистика", callback_data='stats')
            ],
            [
                InlineKeyboardButton("🚀 Инвестиции", callback_data='investments'),
                InlineKeyboardButton("👥 Партнёры", callback_data='referral')
            ],
            [
                InlineKeyboardButton("💸 Вывод средств", callback_data='withdraw'),
                InlineKeyboardButton("🎁 Ежедневный бонус", callback_data='bonus')
            ],
            [
                InlineKeyboardButton("🏆 Рейтинг", callback_data='top'),
                InlineKeyboardButton("💡 Как заработать", callback_data='info')
            ],
            [
                InlineKeyboardButton("📢 Наш канал", url=CHANNEL_LINK),
                InlineKeyboardButton("📋 История", callback_data='history')
            ]
        ]
        
        if is_admin:
            keyboard.append([
                InlineKeyboardButton("👑 АДМИН-ПАНЕЛЬ", callback_data='admin_panel')
            ])
        
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def build_admin_keyboard() -> InlineKeyboardMarkup:
        """Построить клавиатуру админ-панели с улучшенным дизайном"""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Подробная статистика", callback_data='admin_stats'),
                InlineKeyboardButton("👥 Управление пользователями", callback_data='admin_users')
            ],
            [
                InlineKeyboardButton("📢 Массовая рассылка", callback_data='admin_broadcast'),
                InlineKeyboardButton("💌 Личное сообщение", callback_data='admin_send_user')
            ],
            [
                InlineKeyboardButton("💰 Заявки на вывод", callback_data='admin_withdrawals'),
                InlineKeyboardButton("📈 Управление инвестициями", callback_data='admin_investments')
            ],
            [
                InlineKeyboardButton("🚫 Заблокировать", callback_data='admin_block'),
                InlineKeyboardButton("✅ Разблокировать", callback_data='admin_unblock')
            ],
            [
                InlineKeyboardButton("🏠 Главное меню", callback_data='menu')
            ]
        ])
    
    @staticmethod
    def build_payment_keyboard(amount: int) -> InlineKeyboardMarkup:
        """Построить клавиатуру выбора способа оплаты"""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💳 Банковская карта", callback_data=f'payment_card_{amount}'),
                InlineKeyboardButton("🥝 QIWI Кошелёк", callback_data=f'payment_qiwi_{amount}')
            ],
            [
                InlineKeyboardButton("💛 ЮMoney", callback_data=f'payment_ymoney_{amount}'),
                InlineKeyboardButton("₿ Криптовалюта", callback_data=f'payment_crypto_{amount}')
            ],
            [
                InlineKeyboardButton("🔙 Назад", callback_data='withdraw')
            ]
        ])
    
    @staticmethod
    def build_back_keyboard(callback_data: str = 'menu') -> InlineKeyboardMarkup:
        """Построить клавиатуру с кнопкой назад"""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 Главное меню", callback_data=callback_data)
        ]])
    
    @staticmethod
    def build_confirmation_keyboard(action: str, data: str = "") -> InlineKeyboardMarkup:
        """Построить клавиатуру подтверждения действия"""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f'confirm_{action}_{data}'),
                InlineKeyboardButton("❌ Отмена", callback_data='menu')
            ]
        ])

class TelegramBot:
    """Основной класс бота с улучшениями"""
    
    def __init__(self):
        self.logger = BotLogger.setup_logging()
        self.db = Database()
        self.user_service = UserService(self.db)
        self.bonus_service = BonusService(self.db)
        self.withdrawal_service = WithdrawalService(self.db)
        
        self.logger.info("🚀 Bot initialized successfully")
    
    def setup_handlers(self, application: Application) -> None:
        """Настройка обработчиков команд"""
        # Основные команды
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("admin", handle_admin_command))
        
        # Обработчики callback кнопок
        application.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Обработчик текстовых сообщений для админов
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            handle_admin_message
        ))
        
        self.logger.info("✅ Handlers configured successfully")
    
    async def post_init(self, application: Application) -> None:
        """Инициализация после запуска"""
        try:
            # Инициализация базы данных
            self.db.init_db()
            self.logger.info("✅ Database initialized")
            
            # Получение информации о боте
            bot_info = await application.bot.get_me()
            self.logger.info(f"✅ Bot @{bot_info.username} started successfully")
            
        except Exception as e:
            self.logger.error(f"❌ Error in post_init: {e}")
            raise
    
    async def cleanup(self, application: Application) -> None:
        """Очистка ресурсов при завершении"""
        try:
            if hasattr(self.db, 'session') and self.db.session:
                self.db.session.close()
            self.logger.info("✅ Resources cleaned up")
        except Exception as e:
            self.logger.error(f"❌ Error in cleanup: {e}")
    
    async def handle_daily_bonus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка ежедневного бонуса с улучшениями"""
        try:
            user_id = update.callback_query.from_user.id
            user = self.db.get_user(user_id)
            
            if not user:
                await update.callback_query.edit_message_text(
                    text="❌ Пользователь не найден. Пожалуйста, начните с /start.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            can_claim, time_left = self.bonus_service.can_claim_daily_bonus(user)
            
            if not can_claim:
                hours = int(time_left.total_seconds() / 3600)
                minutes = int((time_left.total_seconds() % 3600) / 60)
                
                await update.callback_query.answer(
                    f"⏳ Следующий бонус через {hours}ч {minutes}мин",
                    show_alert=True
                )
                return
            
            if self.bonus_service.claim_daily_bonus(user):
                # Рассчитываем серию дней
                streak = self._calculate_bonus_streak(user)
                
                bonus_text = MessageBuilder.build_bonus_message(DAILY_BONUS, user.balance, streak)
                keyboard = KeyboardBuilder.build_back_keyboard('menu')
                
                await update.callback_query.edit_message_text(
                    bonus_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.callback_query.answer("❌ Ошибка при начислении бонуса", show_alert=True)
                
        except Exception as e:
            self.logger.error(f"Error in handle_daily_bonus: {e}")
            await self._send_error_message(update, "Ошибка при получении бонуса")
    
    def _calculate_bonus_streak(self, user: User) -> int:
        """Рассчитать серию дней получения бонуса"""
        # Простая реализация - можно улучшить
        if user.last_bonus:
            days_diff = (datetime.now() - user.last_bonus).days
            return max(1, 7 - days_diff) if days_diff <= 1 else 1
        return 1
    
    async def show_admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать админ панель с расширенной статистикой"""
        try:
            query = update.callback_query
            user_id = query.from_user.id
            
            if not self.user_service.is_admin(user_id):
                await query.answer("❌ Недостаточно прав", show_alert=True)
                return

            stats = self.db.get_user_statistics()
            # Добавляем дополнительную статистику
            stats.update({
                'total_balance': sum(u.balance for u in self.db.session.query(User).all()),
                'total_withdrawals': sum(u.withdrawals for u in self.db.session.query(User).all()),
                'total_investments': sum(u.total_invested for u in self.db.session.query(User).all()),
                'new_today': self.db.get_new_users_today_count()
            })
            
            stats_text = MessageBuilder.build_admin_panel_message(stats)
            keyboard = KeyboardBuilder.build_admin_keyboard()

            if update.callback_query:
                await query.edit_message_text(
                    stats_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    stats_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            self.logger.error(f"Error in show_admin_panel: {e}")
            await self._send_error_message(update, "Ошибка при загрузке админ-панели")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /start с улучшениями"""
        try:
            # Временная функция для получения ID чата
            if update.message and update.message.chat.type in ['group', 'supergroup']:
                await update.message.reply_text(
                    f"🆔 ID этого чата: `{update.message.chat.id}`", 
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            user_id = update.effective_user.id
            user_name = update.effective_user.first_name or "Друг"
            ref = context.args[0] if context.args else None
            
            # Проверка на блокировку
            if self.user_service.is_blocked(user_id):
                blocked_text = """🚫 *ДОСТУП ОГРАНИЧЕН*

❌ Ваш аккаунт временно заблокирован администрацией.

📞 Для разблокировки обратитесь в поддержку:
└ Напишите администратору с объяснением ситуации

⚠️ Блокировка может быть связана с нарушением правил использования бота."""
                
                if update.message:
                    await update.message.reply_text(blocked_text, parse_mode=ParseMode.MARKDOWN)
                return
            
            # Получаем или создаем пользователя
            user = self.db.get_user(user_id)
            if not user:
                ref_id = int(ref) if ref and ref.isdigit() else None
                user = await self.user_service.create_user(user_id, ref_id)
            
            # Проверка подписки на канал (админы проходят без проверки)
            if not self.user_service.is_admin(user_id):
                is_subscribed = await check_channel_subscription(context, user_id)
                if not is_subscribed:
                    await show_channel_check(update, context)
                    return
            
            # Обновляем статус подписки
            if not user.channel_joined:
                user.channel_joined = True
                self.db.session.commit()
            
            welcome_text = MessageBuilder.build_welcome_message(user, user_name)
            keyboard = KeyboardBuilder.build_main_keyboard(self.user_service.is_admin(user_id))

            if update.callback_query:
                await update.callback_query.edit_message_text(
                    welcome_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    welcome_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            self.logger.error(f"Error in start command: {e}")
            await self._send_error_message(update, "Ошибка при запуске бота")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка нажатий кнопок с улучшенной маршрутизацией"""
        try:
            query = update.callback_query
            await query.answer()
            user_id = query.from_user.id
            
            # Проверка на блокировку
            if self.user_service.is_blocked(user_id):
                await query.answer("❌ Вы заблокированы в боте", show_alert=True)
                return
            
            # Проверка существования пользователя
            user = self.db.get_user(user_id)
            if not user:
                await query.edit_message_text(
                    "❌ Пожалуйста, начните сначала с команды /start",
                    reply_markup=KeyboardBuilder.build_back_keyboard('menu')
                )
                return
            
            # Проверка подписки на канал для обычных пользователей
            if query.data != 'check_subscription' and not self.user_service.is_admin(user_id):
                is_subscribed = await check_channel_subscription(context, user_id)
                if not is_subscribed:
                    await show_channel_check(update, context)
                    return
            
            # Маршрутизация команд
            await self._route_callback(update, context, query.data)
            
        except Exception as e:
            self.logger.error(f"Error in button_handler: {e}")
            await self._send_error_message(update, "Ошибка при обработке команды")
    
    async def _route_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
        """Улучшенная маршрутизация callback команд"""
        user_id = update.effective_user.id
        
        # Админ функции
        if data == 'admin_panel' and self.user_service.is_admin(user_id):
            await self.show_admin_panel(update, context)
        elif data == 'admin_stats' and self.user_service.is_admin(user_id):
            await self._show_detailed_stats(update, context)
        
        # Пользовательские функции
        elif data == 'balance':
            await show_balance(update, context)
        elif data == 'stats':
            await self._show_user_stats(update, context)
        
        # Инвестиции - улучшенная маршрутизация
        elif data == 'investments':
            await show_investments(update, context)
        elif data.startswith(('invest_', 'confirm_invest_', 'calc_')):
            await handle_investment_request(update, context)
        
        # Вывод средств
        elif data == 'withdraw':
            await handle_withdraw_request(update, context)
        elif data.startswith('withdraw_'):
            amount = int(data.split('_')[1])
            await handle_withdraw_request(update, context, amount)
        elif data.startswith('payment_'):
            parts = data.split('_')
            method, amount = parts[1], int(parts[2])
            await handle_payment_details(update, context, method, amount)
        
        # Остальные функции
        elif data == 'bonus':
            await self.handle_daily_bonus(update, context)
        elif data == 'referral':
            await show_referral_program(update, context)
        elif data == 'top':
            await self._show_top_users(update, context)
        elif data == 'info':
            await self._show_info(update, context)
        elif data == 'history':
            await self._show_withdrawal_history(update, context)
        elif data == 'menu':
            await self.start(update, context)
        else:
            await self._handle_unknown_callback(update, context)
    
    async def _show_user_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать расширенную статистику пользователя"""
        user_id = update.effective_user.id
        user = self.db.get_user(user_id)
        
        if not user:
            await update.callback_query.edit_message_text(
                text="❌ Пользователь не найден. Пожалуйста, начните с /start.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        stats_text = MessageBuilder.build_stats_message(user)
        keyboard = KeyboardBuilder.build_back_keyboard('menu')
        
        await update.callback_query.edit_message_text(
            stats_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def _show_detailed_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать подробную статистику для админов"""
        stats = self.db.get_user_statistics()
        invest_stats = self.db.get_investments_statistics()
        
        stats_text = f"""📊 *ПОДРОБНАЯ СТАТИСТИКА СИСТЕМЫ*

👥 *Пользователи:*
├ Всего зарегистрировано: *{stats['total_users']:,}*
├ Активных пользователей: *{stats['active_users']:,}*
├ Заблокированных: *{stats['blocked_users']:,}*
├ Новых за сегодня: *{stats.get('new_today', 0):,}*
└ Подписанных на канал: *{stats.get('subscribed_users', 0):,}*

💰 *Финансовая статистика:*
├ Общий баланс пользователей: *{format_currency(stats.get('total_balance', 0))}*
├ Всего выплачено: *{format_currency(stats.get('total_withdrawals', 0))}*
├ Всего инвестировано: *{format_currency(invest_stats.get('total_investments', 0))}*
├ Прибыль выплачена: *{format_currency(invest_stats.get('total_profit_paid', 0))}*
└ Активных инвестиций: *{invest_stats.get('active_investments', 0):,}*

📈 *Активность:*
├ Заявок на вывод: *{stats.get('pending_withdrawals', 0):,}*
├ Реферальных связей: *{stats.get('total_referrals', 0):,}*
└ Средний доход на пользователя: *{format_currency(stats.get('avg_earnings', 0))}*

🕐 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"""

        keyboard = KeyboardBuilder.build_back_keyboard('admin_panel')
        await update.callback_query.edit_message_text(
            stats_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def _show_top_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать топ пользователей с улучшенным дизайном"""
        try:
            top_users = self.db.session.query(User)\
                .order_by(User.total_earned.desc(), User.balance.desc())\
                .limit(10)\
                .all()
            
            top_text = "🏆 *ТОП-10 УСПЕШНЫХ ПОЛЬЗОВАТЕЛЕЙ*\n\n"
            
            medals = ["🥇", "🥈", "🥉"] + [f"{i}️⃣" for i in range(4, 11)]
            
            for i, user in enumerate(top_users):
                try:
                    chat = await context.bot.get_chat(user.user_id)
                    name = chat.first_name[:15] + "..." if len(chat.first_name) > 15 else chat.first_name
                except:
                    name = f"Пользователь {user.user_id}"
                
                refs_count = len(user.referrals)
                investments_count = len([inv for inv in user.investments if not inv.is_finished])
                
                top_text += f"{medals[i]} *{name}*\n"
                top_text += f"├ Заработано: *{format_currency(user.total_earned)}*\n"
                top_text += f"├ Рефералов: *{refs_count}*\n"
                top_text += f"└ Активных инвестиций: *{investments_count}*\n\n"
            
            top_text += "💡 *Станьте частью топа! Приглашайте друзей и инвестируйте.*"
            
            keyboard = KeyboardBuilder.build_back_keyboard('menu')
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    top_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    top_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            self.logger.error(f"Error in _show_top_users: {e}")
            await self._send_error_message(update, "Ошибка при загрузке рейтинга")
    
    async def _show_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать информацию о заработке"""
        info_text = MessageBuilder.build_info_message()
        keyboard = KeyboardBuilder.build_back_keyboard('menu')
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                info_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                info_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def _show_withdrawal_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать историю выводов с пагинацией"""
        try:
            user_id = update.effective_user.id
            user = self.db.get_user(user_id)
            withdrawals = self.db.session.query(WithdrawalRequest)\
                .filter_by(user_id=user.user_id)\
                .order_by(WithdrawalRequest.date.desc())\
                .limit(10)\
                .all()
            
            if not withdrawals:
                history_text = f"""📋 *ИСТОРИЯ ВЫВОДОВ*

❌ У вас пока нет заявок на вывод средств

💡 Минимальная сумма для вывода: {MIN_WITHDRAW:,}₽
🚀 Начните зарабатывать уже сегодня!"""
            else:
                history_text = "📋 *ИСТОРИЯ ВЫВОДОВ*\n\n"
                
                total_requested = sum(w.amount for w in withdrawals)
                approved_count = len([w for w in withdrawals if w.status == 'approved'])
                
                history_text += f"📊 *Общая статистика:*\n"
                history_text += f"├ Всего заявок: *{len(withdrawals)}*\n"
                history_text += f"├ Одобрено: *{approved_count}*\n"
                history_text += f"└ Сумма заявок: *{format_currency(total_requested)}*\n\n"
                
                for w in withdrawals[:5]:  # Показываем только последние 5
                    status_emoji = {
                        'pending': '⏳',
                        'approved': '✅',
                        'rejected': '❌'
                    }.get(w.status, '❓')
                    
                    status_text = {
                        'pending': 'В обработке',
                        'approved': 'Одобрена',
                        'rejected': 'Отклонена'
                    }.get(w.status, 'Неизвестно')
                    
                    history_text += f"🆔 *Заявка #{w.id}*\n"
                    history_text += f"├ Сумма: *{format_currency(w.amount)}*\n"
                    history_text += f"├ Система: *{w.method.upper()}*\n"
                    history_text += f"├ Дата: {w.date.strftime('%d.%m.%Y %H:%M')}\n"
                    history_text += f"└ Статус: {status_emoji} {status_text}\n\n"
            
            keyboard = KeyboardBuilder.build_back_keyboard('menu')
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    history_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    history_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            self.logger.error(f"Error in _show_withdrawal_history: {e}")
            await self._send_error_message(update, "Ошибка при загрузке истории")
    
    async def _handle_unknown_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка неизвестных callback команд"""
        await update.callback_query.answer("❓ Неизвестная команда", show_alert=True)
        await self.start(update, context)
    
    async def _send_error_message(self, update: Update, error_text: str) -> None:
        """Отправка сообщения об ошибке"""
        try:
            error_message = f"❌ {error_text}\n\n🔄 Попробуйте еще раз или обратитесь в поддержку."
            keyboard = KeyboardBuilder.build_back_keyboard('menu')
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    error_message,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    error_message,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            self.logger.error(f"Error sending error message: {e}")

# ... existing code ...

async def start_webhook(application, webhook_url, port):
    """Запуск бота в режиме webhook"""
    try:
        # Удаляем предыдущие вебхуки
        await application.bot.delete_webhook(drop_pending_updates=True)
        
        # Формируем URL для вебхука
        webhook_url = f"{webhook_url.rstrip('/')}/{TOKEN}"
        logger.info(f"Setting webhook to: {webhook_url}")
        
        # Устанавливаем вебхук
        await application.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True
        )
        
        # Инициализируем приложение
        await application.initialize()
        await application.start()
        
        # Настраиваем веб-сервер для webhook
        await application.updater.start_webhook(
            listen='0.0.0.0',
            port=port,
            url_path=TOKEN,
            webhook_url=webhook_url,
            drop_pending_updates=True
        )
        
        logger.info(f"Webhook server started on port {port}")
        return True
    except Exception as e:
        logger.error(f"Error in start_webhook: {e}")
        return False

async def main():
    """Главная функция запуска бота"""
    application = None
    cron_server = None
    
    # Получаем порт из переменных окружения
    port = int(os.getenv('PORT', '4000'))
    
    try:
        # Создаем экземпляр бота
        telegram_bot = TelegramBot()
        
        # Создаем приложение
        application = Application.builder().token(TOKEN).build()
        
        # Настраиваем обработчики
        telegram_bot.setup_handlers(application)
        
        # Настраиваем хуки жизненного цикла
        application.post_init = telegram_bot.post_init
        application.post_shutdown = telegram_bot.cleanup
        
        # Получаем URL приложения из переменных окружения
        app_url = os.getenv('RENDER_EXTERNAL_URL')
        
        telegram_bot.logger.info("🚀 Starting telegram bot...")
        
        # Определяем режим работы
        if app_url and os.getenv('RENDER'):
            # Режим webhook для Render
            telegram_bot.logger.info("📡 Starting in webhook mode...")
            
            # Настройка порта для Render
            port = int(os.getenv('PORT', '3000'))
            telegram_bot.logger.info(f"🔌 Using port: {port}")
            
            # Формируем базовый URL для вебхука
            base_url = app_url.rstrip('/')
            telegram_bot.logger.info(f"🌐 Base URL: {base_url}")
            
            # Запускаем cron сервер для автоматических начислений
            try:
                cron_server = CronServer(app_url)
                await cron_server.start()
                telegram_bot.logger.info("⏰ Cron server started")
            except Exception as e:
                telegram_bot.logger.warning(f"⚠️ Failed to start cron server: {e}")
            
            # Запускаем webhook
            telegram_bot.logger.info("🔄 Starting webhook...")
            server_started = await start_webhook(application, base_url, port)
            
            if not server_started:
                telegram_bot.logger.error("❌ Failed to start webhook server")
                return
                
            telegram_bot.logger.info(f"✅ Webhook server started successfully on port {port}")
            
            # Бесконечный цикл для поддержания работы
            try:
                while True:
                    await asyncio.sleep(3600)  # Проверяем каждый час
            except KeyboardInterrupt:
                telegram_bot.logger.info("🛑 Bot stopped by user")
                
        else:
            # Режим long polling для локальной разработки
            telegram_bot.logger.info("🔄 Starting in polling mode...")
            
            # Запускаем cron сервер (если нужен)
            try:
                if app_url:
                    cron_server = CronServer(app_url)
                    await cron_server.start()
                    telegram_bot.logger.info("⏰ Cron server started")
            except Exception as e:
                telegram_bot.logger.warning(f"⚠️ Failed to start cron server: {e}")
            
            # Запускаем polling
            await application.run_polling(
                poll_interval=1.0,
                timeout=10,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                drop_pending_updates=True
            )
        
    except KeyboardInterrupt:
        telegram_bot.logger.info("🛑 Bot stopped by user")
    except Exception as e:
        telegram_bot.logger.critical(f"💥 Critical error in main: {e}")
        
        # Отправляем уведомление админам об ошибке
        if application:
            error_message = f"""🚨 *КРИТИЧЕСКАЯ ОШИБКА БОТА* [WARNING]

Бот остановлен из-за критической ошибки:"""
