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
from config.settings import *  # Убедитесь, что TOKEN, ADMIN_IDS и другие константы здесь
from utils.database import Database
from utils.cron_server import CronServer # Предполагается, что этот класс есть и корректен
from utils.helpers import format_currency
from models.user import User, WithdrawalRequest, Investment

# Настройка логгера (будет переопределен в BotLogger, но оставим для модулей, если они его используют)
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
                user_id=user.id, # Связь по user.id (PK), а не user.user_id (TG ID) если модель так настроена
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
                withdrawal.user.withdrawals += withdrawal.amount # Предполагаем, что есть связь user
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
        # Для ref_earnings и invest_earnings, убедитесь, что связи referrals и investments корректно настроены
        # и содержат поля bonus_paid и total_profit соответственно.
        ref_earnings = sum(r.bonus_paid for r in user.referrals if hasattr(r, 'bonus_paid'))
        invest_earnings = sum(i.total_profit for i in user.investments if hasattr(i, 'total_profit'))
        active_investments = len([i for i in user.investments if hasattr(i, 'is_finished') and not i.is_finished])

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
        application.add_handler(CommandHandler("admin", handle_admin_command)) # Убедитесь, что handle_admin_command принимает (update, context)

        # Обработчики callback кнопок
        application.add_handler(CallbackQueryHandler(self.button_handler))

        # Обработчик текстовых сообщений для админов
        # Предполагается, что handle_admin_message также принимает (update, context)
        # и имеет логику для определения, является ли сообщение от админа и требует ли обработки
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_admin_message
        ))

        self.logger.info("✅ Handlers configured successfully")

    async def post_init(self, application: Application) -> None:
        """Инициализация после запуска"""
        try:
            # Инициализация базы данных
            self.db.init_db() # Убедитесь, что этот метод существует и корректен
            self.logger.info("✅ Database initialized")

            # Получение информации о боте
            bot_info = await application.bot.get_me()
            self.logger.info(f"✅ Bot @{bot_info.username} started successfully")

        except Exception as e:
            self.logger.error(f"❌ Error in post_init: {e}", exc_info=True)
            raise

    async def cleanup(self, application: Application) -> None:
        """Очистка ресурсов при завершении"""
        try:
            if hasattr(self.db, 'session') and self.db.session:
                self.db.session.close()
            self.logger.info("✅ Resources cleaned up (DB session closed)")
        except Exception as e:
            self.logger.error(f"❌ Error in cleanup: {e}", exc_info=True)

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

            if not can_claim and time_left: # Добавил проверку time_left
                hours = int(time_left.total_seconds() // 3600)
                minutes = int((time_left.total_seconds() % 3600) // 60)

                await update.callback_query.answer(
                    f"⏳ Следующий бонус через {hours}ч {minutes}мин",
                    show_alert=True
                )
                return
            elif not can_claim: # Случай, если time_left is None, хотя не должен быть при can_claim=False
                 await update.callback_query.answer("⏳ Бонус пока недоступен.", show_alert=True)
                 return


            if self.bonus_service.claim_daily_bonus(user):
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
            self.logger.error(f"Error in handle_daily_bonus: {e}", exc_info=True)
            await self._send_error_message(update, "Ошибка при получении бонуса")

    def _calculate_bonus_streak(self, user: User) -> int:
        """Рассчитать серию дней получения бонуса"""
        # Простая реализация - можно улучшить
        # Эта логика может быть не совсем корректной для "серии"
        # Если бонус только что взят, user.last_bonus == datetime.now()
        # Streak должен храниться в БД и инкрементироваться/сбрасываться
        # Пока оставляю вашу логику, но стоит пересмотреть.
        if user.last_bonus:
            # Чтобы считать серию, нужно знать предыдущее значение last_bonus до обновления.
            # Текущая реализация неверна для подсчета серии.
            # Для простоты пока всегда возвращаем 1, если нет поля streak в User.
            # Или, если есть поле user.bonus_streak: return user.bonus_streak
            return 1 # ЗАГЛУШКА. Требует доработки логики и, возможно, поля в БД.
        return 1

    async def show_admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать админ панель с расширенной статистикой"""
        try:
            query = update.callback_query
            user_id = query.from_user.id

            if not self.user_service.is_admin(user_id):
                await query.answer("❌ Недостаточно прав", show_alert=True)
                return

            stats = self.db.get_user_statistics() # Убедитесь, что метод существует
            # Добавляем дополнительную статистику
            stats.update({
                'total_balance': sum(u.balance for u in self.db.session.query(User).all()),
                'total_withdrawals': sum(u.withdrawals for u in self.db.session.query(User).all()),
                'total_investments': sum(u.total_invested for u in self.db.session.query(User).all()),
                'new_today': self.db.get_new_users_today_count() # Убедитесь, что метод существует
            })

            stats_text = MessageBuilder.build_admin_panel_message(stats)
            keyboard = KeyboardBuilder.build_admin_keyboard()

            await query.edit_message_text( # В оригинальном коде был if update.callback_query, но здесь всегда callback
                stats_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            self.logger.error(f"Error in show_admin_panel: {e}", exc_info=True)
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

            if self.user_service.is_blocked(user_id):
                blocked_text = """🚫 *ДОСТУП ОГРАНИЧЕН*

❌ Ваш аккаунт временно заблокирован администрацией.

📞 Для разблокировки обратитесь в поддержку:
└ Напишите администратору с объяснением ситуации

⚠️ Блокировка может быть связана с нарушением правил использования бота."""
                if update.message:
                    await update.message.reply_text(blocked_text, parse_mode=ParseMode.MARKDOWN)
                elif update.callback_query: # Если /start вызван из callback (например, 'menu')
                    await update.callback_query.message.reply_text(blocked_text, parse_mode=ParseMode.MARKDOWN)
                    await update.callback_query.answer() # Закрыть callback
                return

            user = self.db.get_user(user_id)
            if not user:
                ref_id = int(ref) if ref and ref.isdigit() else None
                user = await self.user_service.create_user(user_id, ref_id)

            if not self.user_service.is_admin(user_id):
                is_subscribed = await check_channel_subscription(context, user_id)
                if not is_subscribed:
                    await show_channel_check(update, context)
                    return

            if not user.channel_joined: # Предполагается, что у User есть поле channel_joined
                user.channel_joined = True
                self.db.session.commit()

            welcome_text = MessageBuilder.build_welcome_message(user, user_name)
            keyboard = KeyboardBuilder.build_main_keyboard(self.user_service.is_admin(user_id))

            if update.callback_query: # Если /start вызван из callback (например, 'menu')
                await update.callback_query.edit_message_text(
                    welcome_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
            elif update.message:
                await update.message.reply_text(
                    welcome_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            self.logger.error(f"Error in start command: {e}", exc_info=True)
            await self._send_error_message(update, "Ошибка при запуске бота")

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка нажатий кнопок с улучшенной маршрутизацией"""
        try:
            query = update.callback_query
            await query.answer() # Отвечаем на callback сразу
            user_id = query.from_user.id

            if self.user_service.is_blocked(user_id):
                # Можно показать сообщение об ошибке прямо в callback alert
                # await query.answer("❌ Вы заблокированы в боте", show_alert=True)
                # Или отправить сообщение, если это более информативно
                await query.message.reply_text("🚫 Вы заблокированы и не можете использовать бота.")
                return

            user = self.db.get_user(user_id)
            if not user:
                await query.edit_message_text(
                    "❌ Пожалуйста, начните сначала с команды /start",
                    reply_markup=KeyboardBuilder.build_back_keyboard('menu') # 'menu' вызовет /start
                )
                return

            if query.data != 'check_subscription' and not self.user_service.is_admin(user_id):
                is_subscribed = await check_channel_subscription(context, user_id)
                if not is_subscribed:
                    await show_channel_check(update, context) # show_channel_check должен сам обработать update (query или message)
                    return

            await self._route_callback(update, context, query.data)

        except Exception as e:
            self.logger.error(f"Error in button_handler: {e}", exc_info=True)
            await self._send_error_message(update, "Ошибка при обработке команды")

    async def _route_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
        """Улучшенная маршрутизация callback команд"""
        user_id = update.effective_user.id

        if data == 'admin_panel' and self.user_service.is_admin(user_id):
            await self.show_admin_panel(update, context)
        elif data == 'admin_stats' and self.user_service.is_admin(user_id):
            await self._show_detailed_stats(update, context)
        elif data == 'balance':
            await show_balance(update, context)
        elif data == 'stats':
            await self._show_user_stats(update, context)
        elif data == 'investments':
            await show_investments(update, context)
        elif data.startswith(('invest_', 'confirm_invest_', 'calc_')):
            await handle_investment_request(update, context)
        elif data == 'withdraw':
            await handle_withdraw_request(update, context)
        elif data.startswith('withdraw_') and data.split('_')[1].isdigit(): # Проверка что это число
            amount = int(data.split('_')[1])
            await handle_withdraw_request(update, context, amount=amount) # Передаем amount как именованный аргумент
        elif data.startswith('payment_'):
            parts = data.split('_')
            if len(parts) == 3 and parts[2].isdigit(): # payment_method_amount
                method, amount_str = parts[1], parts[2]
                amount = int(amount_str)
                await handle_payment_details(update, context, payment_method=method, amount=amount)
            else:
                self.logger.warning(f"Invalid payment callback data: {data}")
                await self._handle_unknown_callback(update, context)
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
            await self.start(update, context) # 'menu' теперь вызывает start
        elif data == 'check_subscription': # Обработка кнопки проверки подписки
             # Логика проверки подписки уже должна была выполниться в show_channel_check
             # Если пользователь нажал кнопку после подписки, его должно перенаправить
             is_subscribed = await check_channel_subscription(context, user_id)
             if is_subscribed:
                user = self.db.get_user(user_id)
                if user and not user.channel_joined:
                    user.channel_joined = True
                    self.db.session.commit()
                await self.start(update, context) # Отправляем на главный экран
             else:
                await update.callback_query.answer("Вы все еще не подписаны на канал.", show_alert=True)
                # Можно повторно показать сообщение с просьбой подписаться, если show_channel_check это не делает
                # await show_channel_check(update, context)
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
        # Убедитесь, что эти методы существуют в self.db
        stats = self.db.get_user_statistics()
        invest_stats = self.db.get_investments_statistics()

        # Дополняем статистику, если каких-то ключей нет
        stats.setdefault('new_today', self.db.get_new_users_today_count())
        stats.setdefault('total_balance', sum(u.balance for u in self.db.session.query(User).all()))
        stats.setdefault('total_withdrawals', sum(u.withdrawals for u in self.db.session.query(User).all()))
        # 'subscribed_users' - если такой метод есть
        # stats.setdefault('subscribed_users', self.db.get_subscribed_users_count())
        
        invest_stats.setdefault('total_investments', sum(u.total_invested for u in self.db.session.query(User).all()))
        # invest_stats.setdefault('total_profit_paid', ...)
        # invest_stats.setdefault('active_investments', ...)

        stats_text = f"""📊 *ПОДРОБНАЯ СТАТИСТИКА СИСТЕМЫ*

👥 *Пользователи:*
├ Всего зарегистрировано: *{stats.get('total_users', 0):,}*
├ Активных пользователей: *{stats.get('active_users', 0):,}*
├ Заблокированных: *{stats.get('blocked_users', 0):,}*
├ Новых за сегодня: *{stats.get('new_today', 0):,}*
└ Подписанных на канал: *{stats.get('subscribed_users', 'N/A'):,}*

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
            top_users_db = self.db.session.query(User)\
                .order_by(User.total_earned.desc(), User.balance.desc())\
                .limit(10)\
                .all()

            top_text = "🏆 *ТОП-10 УСПЕШНЫХ ПОЛЬЗОВАТЕЛЕЙ*\n\n"
            medals = ["🥇", "🥈", "🥉"] + [f"{i}️⃣" for i in range(4, 11)]

            for i, user_db in enumerate(top_users_db):
                name = f"Пользователь ID {user_db.user_id}" # Изначально
                try:
                    chat = await context.bot.get_chat(user_db.user_id)
                    # Используем first_name или username, если есть
                    display_name = chat.first_name or chat.username or f"ID {user_db.user_id}"
                    name = display_name[:20] + "..." if len(display_name) > 20 else display_name
                except TelegramError as e: # Более конкретное исключение
                    self.logger.warning(f"Could not get chat for user {user_db.user_id}: {e}")
                except Exception as e:
                    self.logger.error(f"Unexpected error getting chat for user {user_db.user_id}: {e}")


                refs_count = len(user_db.referrals)
                investments_count = len([inv for inv in user_db.investments if hasattr(inv, 'is_finished') and not inv.is_finished])

                top_text += f"{medals[i]} *{name}*\n"
                top_text += f"├ Заработано: *{format_currency(user_db.total_earned)}*\n"
                top_text += f"├ Рефералов: *{refs_count}*\n"
                top_text += f"└ Активных инвестиций: *{investments_count}*\n\n"

            if not top_users_db:
                top_text += "Пока нет пользователей в рейтинге."

            top_text += "\n💡 *Станьте частью топа! Приглашайте друзей и инвестируйте.*"
            keyboard = KeyboardBuilder.build_back_keyboard('menu')

            if update.callback_query:
                await update.callback_query.edit_message_text(top_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            elif update.message: # Если команда /top была бы
                await update.message.reply_text(top_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            self.logger.error(f"Error in _show_top_users: {e}", exc_info=True)
            await self._send_error_message(update, "Ошибка при загрузке рейтинга")

    async def _show_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать информацию о заработке"""
        info_text = MessageBuilder.build_info_message()
        keyboard = KeyboardBuilder.build_back_keyboard('menu')

        if update.callback_query:
            await update.callback_query.edit_message_text(info_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        elif update.message:
             await update.message.reply_text(info_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


    async def _show_withdrawal_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать историю выводов"""
        try:
            user_id = update.effective_user.id
            user = self.db.get_user(user_id)
            if not user: # Добавил проверку
                 await self._send_error_message(update, "Пользователь не найден.")
                 return

            # В WithdrawalRequest user_id должен быть telegram_id пользователя
            withdrawals = self.db.session.query(WithdrawalRequest)\
                .filter(WithdrawalRequest.user.has(user_id=user.user_id)) # Фильтр по telegram_id связанного юзера
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

                history_text += f"📊 *Общая статистика (последние 10):*\n"
                history_text += f"├ Всего заявок: *{len(withdrawals)}*\n"
                history_text += f"├ Одобрено: *{approved_count}*\n"
                history_text += f"└ Сумма заявок: *{format_currency(total_requested)}*\n\n"

                for w in withdrawals: # Показываем все 10 (или меньше)
                    status_emoji = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}.get(w.status, '❓')
                    status_text = {'pending': 'В обработке', 'approved': 'Одобрена', 'rejected': 'Отклонена'}.get(w.status, 'Неизвестно')

                    history_text += f"🆔 *Заявка #{w.id}*\n"
                    history_text += f"├ Сумма: *{format_currency(w.amount)}*\n"
                    history_text += f"├ Система: *{w.method.upper()}*\n"
                    history_text += f"├ Дата: {w.date.strftime('%d.%m.%Y %H:%M')}\n"
                    history_text += f"└ Статус: {status_emoji} {status_text}\n\n"

            keyboard = KeyboardBuilder.build_back_keyboard('menu')
            if update.callback_query:
                await update.callback_query.edit_message_text(history_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            elif update.message:
                await update.message.reply_text(history_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            self.logger.error(f"Error in _show_withdrawal_history: {e}", exc_info=True)
            await self._send_error_message(update, "Ошибка при загрузке истории")

    async def _handle_unknown_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка неизвестных callback команд"""
        await update.callback_query.answer("❓ Неизвестная команда", show_alert=True)
        # Можно не вызывать start, а просто дать понять, что команда не распознана.
        # await self.start(update, context)

    async def _send_error_message(self, update: Update, error_text: str) -> None:
        """Отправка сообщения об ошибке"""
        try:
            error_message = f"❌ {error_text}\n\n🔄 Попробуйте еще раз или обратитесь в поддержку."
            keyboard = KeyboardBuilder.build_back_keyboard('menu')

            if update.callback_query:
                # Проверяем, можно ли редактировать сообщение
                if update.callback_query.message:
                    await update.callback_query.edit_message_text(
                        error_message,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.MARKDOWN
                    )
                else: # Если сообщение уже удалено или недоступно
                    await update.callback_query.answer(error_text, show_alert=True)
            elif update.message:
                await update.message.reply_text(
                    error_message,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
        except TelegramError as te: # Конкретно ошибки телеграма при отправке
            self.logger.error(f"TelegramError sending error message: {te}")
        except Exception as e:
            self.logger.error(f"Error sending error message: {e}", exc_info=True)

# --- Конец классов и методов TelegramBot ---

async def main():
    """Главная функция запуска бота"""
    application = None
    cron_server = None
    telegram_bot = None # Объявим здесь, чтобы было доступно в finally

    try:
        # Создаем экземпляр бота
        telegram_bot = TelegramBot() # Используем логгер из этого экземпляра
        
        # Создаем приложение
        # Убедитесь, что TOKEN загружен из config.settings
        if not TOKEN:
            telegram_bot.logger.critical("❌ TELEGRAM_BOT_TOKEN не найден! Проверьте config/settings.py или переменные окружения.")
            return
        
        application = Application.builder().token(TOKEN).build()
        
        # Настраиваем обработчики
        telegram_bot.setup_handlers(application)
        
        # Настраиваем хуки жизненного цикла
        application.post_init = telegram_bot.post_init
        application.post_shutdown = telegram_bot.cleanup
        
        app_url = os.getenv('RENDER_EXTERNAL_URL')
        
        telegram_bot.logger.info("🚀 Starting telegram bot...")
        
        if app_url and os.getenv('RENDER'):
            telegram_bot.logger.info("📡 Starting in webhook mode for Render...")
            
            port_str = os.getenv('PORT')
            if not port_str:
                telegram_bot.logger.critical("❌ Переменная окружения PORT не установлена Render!")
                # Можно попробовать порт по умолчанию, но это плохая практика для Render
                # port = 8000 # НЕ РЕКОМЕНДУЕТСЯ ДЛЯ RENDER
                return # Лучше завершить работу, если PORT не задан
            try:
                port = int(port_str)
            except ValueError:
                telegram_bot.logger.critical(f"❌ Неверное значение для PORT: {port_str}")
                return

            webhook_path = TOKEN # Используем токен как секретный путь
            full_webhook_url = f"{app_url.rstrip('/')}/{webhook_path}"

            telegram_bot.logger.info(f"Configuring webhook. URL: {full_webhook_url}, Listen: 0.0.0.0:{port}, Path: /{webhook_path}")
            
            # Запуск CronServer
            try:
                if app_url: # CronServer может требовать app_url
                    # Предполагается, что CronServer - асинхронный или запускается неблокирующим способом
                    cron_server = CronServer(app_url) 
                    await cron_server.start() # Убедитесь, что start() асинхронный или неблокирующий
                    telegram_bot.logger.info("⏰ Cron server started")
            except Exception as e:
                telegram_bot.logger.warning(f"⚠️ Failed to start cron server: {e}", exc_info=True)
            
            # application.run_webhook() сам установит вебхук с Telegram и запустит веб-сервер
            # Это блокирующий вызов
            await application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=webhook_path,
                webhook_url=full_webhook_url,
                drop_pending_updates=True
                # secret_token="YOUR_ACTUAL_SECRET_TOKEN" # Если вы хотите использовать X-Telegram-Bot-Api-Secret-Token
            )
            # После успешного запуска run_webhook, этот код не выполнится, пока сервер не будет остановлен
            # PTB сама выведет логи о запуске uvicorn
            
        else:
            telegram_bot.logger.info("🔄 Starting in polling mode (local development)...")
            
            telegram_bot.logger.info("Attempting to delete any existing webhook...")
            await application.bot.delete_webhook(drop_pending_updates=True)
            telegram_bot.logger.info("Webhook deleted (if existed).")

            try:
                if app_url: # Если есть URL для пинга (может быть ngrok)
                    cron_server = CronServer(app_url)
                    await cron_server.start()
                    telegram_bot.logger.info("⏰ Cron server started (local)")
            except Exception as e:
                telegram_bot.logger.warning(f"⚠️ Failed to start cron server (local): {e}", exc_info=True)
            
            telegram_bot.logger.info("Starting polling...")
            await application.run_polling(
                poll_interval=1.0, # Стандартное значение
                drop_pending_updates=True
            )
        
    except KeyboardInterrupt:
        if telegram_bot: telegram_bot.logger.info("🛑 Bot stopped by user (KeyboardInterrupt)")
        else: print("🛑 Bot stopped by user (KeyboardInterrupt) - logger not initialized")
    except ValueError as ve: 
        if telegram_bot: telegram_bot.logger.critical(f"💥 Critical configuration error: {ve}", exc_info=True)
        else: print(f"💥 Critical configuration error: {ve}")
    except Exception as e:
        if telegram_bot: telegram_bot.logger.critical(f"💥 Critical error in main: {e}", exc_info=True)
        else: print(f"💥 Critical error in main: {e}")
        
        if application and telegram_bot and ADMIN_IDS:
            error_message = f"""🚨 *КРИТИЧЕСКАЯ ОШИБКА БОТА*

Бот остановлен из-за критической ошибки:
`{type(e).__name__}: {str(e)}`

Проверьте логи для деталей."""
            for admin_id in ADMIN_IDS:
                try:
                    await application.bot.send_message(chat_id=admin_id, text=error_message, parse_mode=ParseMode.MARKDOWN)
                except Exception as send_err:
                    if telegram_bot: telegram_bot.logger.error(f"Failed to send critical error notification to admin {admin_id}: {send_err}")
    finally:
        if cron_server and hasattr(cron_server, 'stop') and callable(cron_server.stop):
            if telegram_bot: telegram_bot.logger.info("Stopping cron server...")
            try:
                await cron_server.stop() # Если stop асинхронный
            except TypeError: # Если stop не асинхронный
                 cron_server.stop()
            except Exception as e_cron_stop:
                if telegram_bot: telegram_bot.logger.error(f"Error stopping cron server: {e_cron_stop}")

        # PTB v20+ application.stop() не является публичным API и вызывается автоматически при завершении run_polling/run_webhook
        # if application and hasattr(application, 'stop') and application.running:
        #      if telegram_bot: telegram_bot.logger.info("Stopping application...")
        #      await application.stop()
        if telegram_bot: telegram_bot.logger.info("Bot shutdown process finished.")


if __name__ == '__main__':
    # Убедитесь, что TOKEN и другие настройки (ADMIN_IDS, DATABASE_URL и т.д.)
    # доступны через импорт `from config.settings import *`
    # или загружаются здесь из переменных окружения, например:
    # from dotenv import load_dotenv
    # load_dotenv()
    # TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") # Пример, если бы TOKEN брался из .env
    # if not TOKEN:
    #     print("TELEGRAM_BOT_TOKEN environment variable not set!")
    #     exit(1)
    # ADMIN_IDS = [int(admin_id) for admin_id in os.getenv("ADMIN_IDS", "").split(",") if admin_id.strip()]
    
    # Проверка наличия TOKEN (уже есть в main, но можно и здесь для раннего выхода)
    if not TOKEN:
        print("Переменная TOKEN не найдена. Проверьте config/settings.py или переменные окружения.")
        exit(1)
    if not ADMIN_IDS:
        print("Переменная ADMIN_IDS не найдена или пуста. Проверьте config/settings.py или переменные окружения.")
        # Можно разрешить запуск без ADMIN_IDS, если это допустимо
        # exit(1) 

    asyncio.run(main())
Use code with caution.
Python
Ключевые изменения и на что обратить внимание:
start_webhook удалена: Её функциональность теперь в main() с использованием application.run_webhook().
main() функция:
Webhook Mode (Render):
Получает PORT из os.getenv('PORT'). Важно: Render должен предоставлять эту переменную. Если ее нет, ваш сервис на Render, скорее всего, настроен неправильно (например, как Background Worker, а не Web Service).
Использует application.run_webhook(...). Этот метод сам регистрирует webhook в Telegram и запускает веб-сервер.
listen="0.0.0.0": критически важно для работы в контейнерах/PaaS.
url_path=TOKEN: Используем токен бота как секретный путь для вебхука. Это распространенная практика.
Polling Mode (Local):
Явно удаляет вебхук с помощью await application.bot.delete_webhook(drop_pending_updates=True).
Использует application.run_polling(...).
CronServer: Запуск CronServer оставлен. Убедитесь, что его метод start() является асинхронным (async def start) или неблокирующим, чтобы не мешать основному циклу бота. Аналогично для stop().
Логгирование и обработка ошибок: Улучшено логгирование и обработка критических ошибок с уведомлением администраторов.
telegram_bot: Экземпляр TelegramBot создается в начале try и доступен в except и finally.
if __name__ == '__main__'::
Стандартный блок для запуска Python-скрипта.
Добавлены проверки наличия TOKEN и ADMIN_IDS (которые должны импортироваться из config.settings). Вы можете настроить эту проверку по своему усмотрению.
Зависимости из config.settings: Убедитесь, что все необходимые константы (TOKEN, ADMIN_IDS, REFERRAL_BONUS, DAILY_BONUS, MIN_WITHDRAW, CHANNEL_LINK и т.д.) корректно определены в config/settings.py и импортируются через from config.settings import *.
Мелкие правки в коде бота:
В WithdrawalService.create_withdrawal_request: user_id=user.id (если user.id это PK в вашей модели User, а user.user_id это Telegram ID). Уточните это в соответствии с вашей моделью. Если WithdrawalRequest связывается по Telegram ID, то должно быть user_id=user.user_id (и поле в WithdrawalRequest должно называться соответственно или быть внешним ключом на поле с Telegram ID в User). Судя по filter(WithdrawalRequest.user.has(user_id=user.user_id)) в _show_withdrawal_history, ваша модель WithdrawalRequest имеет связь user, и у этой user есть поле user_id, которое является Telegram ID. Тогда в create_withdrawal_request нужно передавать объект user, а SQLAlchemy сама разберется с внешним ключом: withdrawal = WithdrawalRequest(user=user, ...) или user_id=user.id если FK на User.id. Уточните структуру ваших моделей SQLAlchemy. Я оставил user_id=user.id как более типичное для PK, но filter_by(user_id=user.user_id) в _show_withdrawal_history указывает на другое. Я изменил его на filter(WithdrawalRequest.user.has(user_id=user.user_id)) что более корректно для фильтрации по атрибуту связанной модели.
В _calculate_bonus_streak: Ваша текущая логика не будет корректно считать "серию". Для правильного подсчета серии дней получения бонуса вам нужно хранить счетчик серии в базе данных для каждого пользователя и обновлять/сбрасывать его. Пока что я оставил заглушку, возвращающую 1.
Некоторые вызовы методов self.db (например, get_user_statistics, get_investments_statistics, get_new_users_today_count) должны существовать в вашем классе Database.
В _show_top_users добавлена обработка TelegramError при получении информации о чате.
В _route_callback добавлены проверки для data.startswith('withdraw_') и data.startswith('payment_') на корректность формата.
В handle_daily_bonus добавлена проверка time_left перед использованием.
Этот код должен быть более устойчивым и правильно работать как на Render, так и локально. Тщательно протестируйте его, особенно логику, связанную с базой данных и специфичными методами вашего Database класса.
