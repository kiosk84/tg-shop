from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.database import Database
from models.user import User, Investment
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class PlanType(Enum):
    """Типы инвестиционных планов"""
    STARTER = "starter"
    STANDARD = "standard"
    PREMIUM = "premium"
    VIP = "vip"

@dataclass
class InvestmentPlan:
    """Модель инвестиционного плана"""
    name: str
    min_amount: int
    max_amount: int
    daily_profit: float
    emoji: str
    duration_days: int
    description: str
    features: List[str]
    color: str

class InvestmentConfig:
    """Конфигурация инвестиционных планов"""
    
    PLANS: Dict[str, InvestmentPlan] = {
        PlanType.STARTER.value: InvestmentPlan(
            name="🌱 Стартер",
            min_amount=100,
            max_amount=999,
            daily_profit=0.012,
            emoji="🌱",
            duration_days=30,
            description="Идеальный старт для новичков",
            features=["Минимальный риск", "Стабильный доход", "Поддержка 24/7"],
            color="🟢"
        ),
        PlanType.STANDARD.value: InvestmentPlan(
            name="💎 Стандарт",
            min_amount=1000,
            max_amount=4999,
            daily_profit=0.018,
            emoji="💎",
            duration_days=30,
            description="Оптимальное соотношение риска и доходности",
            features=["Повышенная доходность", "Приоритетная поддержка", "Бонусы"],
            color="🔵"
        ),
        PlanType.PREMIUM.value: InvestmentPlan(
            name="🚀 Премиум",
            min_amount=5000,
            max_amount=19999,
            daily_profit=0.025,
            emoji="🚀",
            duration_days=30,
            description="Для серьёзных инвесторов",
            features=["Высокая доходность", "VIP поддержка", "Эксклюзивные бонусы"],
            color="🟡"
        ),
        PlanType.VIP.value: InvestmentPlan(
            name="👑 VIP",
            min_amount=20000,
            max_amount=100000,
            daily_profit=0.035,
            emoji="👑",
            duration_days=30,
            description="Максимальная доходность для VIP-клиентов",
            features=["Максимальный доход", "Персональный менеджер", "Приватные сигналы"],
            color="🟣"
        )
    }
    
    @classmethod
    def get_plan(cls, plan_type: str) -> Optional[InvestmentPlan]:
        """Получить план по типу"""
        return cls.PLANS.get(plan_type)
    
    @classmethod
    def get_all_plans(cls) -> Dict[str, InvestmentPlan]:
        """Получить все планы"""
        return cls.PLANS
    
    @classmethod
    def get_suitable_plans(cls, amount: int) -> List[Tuple[str, InvestmentPlan]]:
        """Получить подходящие планы для суммы"""
        suitable = []
        for plan_id, plan in cls.PLANS.items():
            if plan.min_amount <= amount <= plan.max_amount:
                suitable.append((plan_id, plan))
        return suitable

class InvestmentService:
    """Сервис для работы с инвестициями"""
    
    def __init__(self):
        self.db = Database()
    
    def get_user_investments_stats(self, user_id: int) -> Dict[str, Any]:
        """Получить детальную статистику инвестиций пользователя"""
        user = self.db.get_user(user_id)
        
        if not user:
            return self._empty_stats()
        
        active_investments = [inv for inv in user.investments if not inv.is_finished]
        completed_investments = [inv for inv in user.investments if inv.is_finished]
        
        total_profit = sum(inv.current_profit for inv in user.investments)
        daily_income = sum(
            inv.amount * inv.daily_profit 
            for inv in active_investments
        )
        
        return {
            'total_invested': user.total_invested,
            'total_profit': total_profit,
            'daily_income': daily_income,
            'active_investments': active_investments,
            'completed_investments': completed_investments,
            'active_count': len(active_investments),
            'completed_count': len(completed_investments),
            'has_investments': len(user.investments) > 0,
            'roi_percentage': (total_profit / user.total_invested * 100) if user.total_invested > 0 else 0
        }
    
    def _empty_stats(self) -> Dict[str, Any]:
        """Пустая статистика для новых пользователей"""
        return {
            'total_invested': 0,
            'total_profit': 0,
            'daily_income': 0,
            'active_investments': [],
            'completed_investments': [],
            'active_count': 0,
            'completed_count': 0,
            'has_investments': False,
            'roi_percentage': 0
        }
    
    def validate_investment(self, user_id: int, plan_type: str, amount: int) -> Dict[str, Any]:
        """Валидация инвестиции"""
        user = self.db.get_user(user_id)
        plan = InvestmentConfig.get_plan(plan_type)
        
        if not user:
            return {'valid': False, 'error': 'Пользователь не найден'}
        
        if not plan:
            return {'valid': False, 'error': 'Неверный план инвестиций'}
        
        if user.balance < amount:
            return {
                'valid': False, 
                'error': f'💸 Недостаточно средств\n\nНеобходимо: {amount:,}₽\nДоступно: {user.balance:,}₽'
            }
        
        if amount < plan.min_amount:
            return {
                'valid': False,
                'error': f'💰 Минимальная сумма для плана "{plan.name}": {plan.min_amount:,}₽'
            }
        
        if amount > plan.max_amount:
            return {
                'valid': False,
                'error': f'💰 Максимальная сумма для плана "{plan.name}": {plan.max_amount:,}₽'
            }
        
        return {'valid': True}
    
    def create_investment(self, user_id: int, plan_type: str, amount: int) -> Dict[str, Any]:
        """Создать новую инвестицию"""
        validation = self.validate_investment(user_id, plan_type, amount)
        if not validation['valid']:
            return {'success': False, 'error': validation['error']}
        
        user = self.db.get_user(user_id)
        plan = InvestmentConfig.get_plan(plan_type)
        
        try:
            # Создаем инвестицию
            investment = Investment(
                user_id=user.id,
                plan_type=plan_type,
                amount=amount,
                daily_profit=plan.daily_profit,
                start_date=datetime.now(),
                end_date=datetime.now() + timedelta(days=plan.duration_days)
            )
            
            # Обновляем баланс пользователя
            user.balance -= amount
            user.total_invested += amount
            user.investments.append(investment)
            
            self.db.session.add(investment)
            self.db.session.commit()
            
            logger.info(f"Investment created: user_id={user_id}, plan={plan_type}, amount={amount}")
            
            return {
                'success': True,
                'investment': investment,
                'plan': plan,
                'expected_profit': amount * plan.daily_profit * plan.duration_days
            }
            
        except Exception as e:
            logger.error(f"Error creating investment: {e}")
            self.db.session.rollback()
            return {'success': False, 'error': 'Произошла ошибка при создании инвестиции'}
    
    def calculate_profit(self, amount: int, plan_type: str) -> Dict[str, float]:
        """Рассчитать прибыль для суммы и плана"""
        plan = InvestmentConfig.get_plan(plan_type)
        if not plan:
            return {}
        
        daily_profit = amount * plan.daily_profit
        total_profit = daily_profit * plan.duration_days
        total_return = amount + total_profit
        
        return {
            'daily_profit': daily_profit,
            'total_profit': total_profit,
            'total_return': total_return,
            'roi_percentage': (total_profit / amount) * 100
        }

class InvestmentMessageBuilder:
    """Строитель сообщений для инвестиций"""
    
    @staticmethod
    def build_main_menu_text() -> str:
        """Построить текст главного меню инвестиций"""
        plans = InvestmentConfig.get_all_plans()
        
        text_parts = [
            "🚀 *ИНВЕСТИЦИОННЫЕ ПЛАНЫ*\n",
            "┌─────────────────────────────┐",
            "│  Выберите подходящий план   │",
            "└─────────────────────────────┘\n"
        ]
        
        for plan_type, plan in plans.items():
            profit_percent = int(plan.daily_profit * 100)
            total_profit = int(plan.daily_profit * plan.duration_days * 100)
            
            text_parts.append(
                f"{plan.color} *{plan.name}*\n"
                f"💰 От {plan.min_amount:,} до {plan.max_amount:,}₽\n"
                f"📈 {profit_percent}% в день • {total_profit}% за месяц\n"
                f"📝 {plan.description}\n"
            )
        
        text_parts.append("💡 *Все планы работают 30 дней с ежедневными выплатами*")
        
        return "\n".join(text_parts)
    
    @staticmethod
    def build_plan_details_text(plan_type: str, amount: int = None) -> str:
        """Построить детальное описание плана"""
        plan = InvestmentConfig.get_plan(plan_type)
        if not plan:
            return "❌ План не найден"
        
        service = InvestmentService()
        calc_amount = amount or plan.min_amount
        profit_calc = service.calculate_profit(calc_amount, plan_type)
        
        text_parts = [
            f"{plan.emoji} *{plan.name.upper()}*\n",
            f"💰 *Сумма инвестиций:* {plan.min_amount:,} - {plan.max_amount:,}₽",
            f"📈 *Ежедневный доход:* {plan.daily_profit * 100}%",
            f"⏱ *Срок инвестиций:* {plan.duration_days} дней",
            f"🎯 *Общая доходность:* {int(plan.daily_profit * plan.duration_days * 100)}%\n",
            
            f"📊 *РАСЧЁТ ДЛЯ {calc_amount:,}₽:*",
            f"├ Ежедневно: +{profit_calc['daily_profit']:,.2f}₽",
            f"├ За месяц: +{profit_calc['total_profit']:,.2f}₽",
            f"└ Итого: {profit_calc['total_return']:,.2f}₽\n",
            
            f"✨ *ПРЕИМУЩЕСТВА:*"
        ]
        
        for feature in plan.features:
            text_parts.append(f"• {feature}")
        
        return "\n".join(text_parts)
    
    @staticmethod
    def build_stats_text(stats: Dict[str, Any]) -> str:
        """Построить текст статистики"""
        if not stats['has_investments']:
            return (
                "📊 *ВАШИ ИНВЕСТИЦИИ*\n\n"
                "🔍 У вас пока нет активных инвестиций\n\n"
                "💡 Начните инвестировать уже сегодня и получайте стабильный пассивный доход!"
            )
        
        text_parts = [
            "📊 *ВАШИ ИНВЕСТИЦИИ*\n",
            f"💰 *Всего инвестировано:* {stats['total_invested']:,}₽",
            f"📈 *Общий доход:* {stats['total_profit']:,}₽",
            f"💵 *Ежедневный доход:* {stats['daily_income']:,}₽",
            f"📊 *ROI:* {stats['roi_percentage']:.1f}%\n",
            
            f"🔄 *Активные:* {stats['active_count']} инвестиций",
            f"✅ *Завершённые:* {stats['completed_count']} инвестиций"
        ]
        
        if stats['active_investments']:
            text_parts.append("\n*🔥 АКТИВНЫЕ ИНВЕСТИЦИИ:*")
            for inv in stats['active_investments'][:5]:  # Показываем только первые 5
                plan = InvestmentConfig.get_plan(inv.plan_type)
                if plan:
                    days_left = (inv.end_date - datetime.now()).days
                    text_parts.append(
                        f"{plan.emoji} {plan.name}: {inv.amount:,}₽ "
                        f"(+{inv.current_profit:,}₽) • {days_left}д"
                    )
        
        return "\n".join(text_parts)
    
    @staticmethod
    def build_success_text(investment, plan: InvestmentPlan, expected_profit: float) -> str:
        """Построить текст успешного создания инвестиции"""
        return (
            f"🎉 *ИНВЕСТИЦИЯ СОЗДАНА!*\n\n"
            f"{plan.emoji} *План:* {plan.name}\n"
            f"💰 *Сумма:* {investment.amount:,}₽\n"
            f"📈 *Ежедневно:* +{investment.amount * plan.daily_profit:,.2f}₽\n"
            f"🎯 *Ожидаемый доход:* {expected_profit:,.2f}₽\n"
            f"📅 *Завершение:* {investment.end_date.strftime('%d.%m.%Y')}\n\n"
            f"✅ Первая выплата поступит завтра в 12:00 МСК"
        )

class InvestmentKeyboardBuilder:
    """Строитель клавиатур для инвестиций"""
    
    @staticmethod
    def build_main_menu_keyboard() -> InlineKeyboardMarkup:
        """Построить клавиатуру главного меню"""
        plans = InvestmentConfig.get_all_plans()
        keyboard = []
        
        # Добавляем кнопки планов
        for plan_type, plan in plans.items():
            keyboard.append([
                InlineKeyboardButton(
                    f"{plan.emoji} {plan.name} • от {plan.min_amount:,}₽",
                    callback_data=f'invest_plan_{plan_type}'
                )
            ])
        
        # Дополнительные кнопки
        keyboard.extend([
            [
                InlineKeyboardButton("📊 Мои инвестиции", callback_data='invest_stats'),
                InlineKeyboardButton("🧮 Калькулятор", callback_data='invest_calc')
            ],
            [InlineKeyboardButton("🏠 Главное меню", callback_data='menu')]
        ])
        
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def build_plan_keyboard(plan_type: str) -> InlineKeyboardMarkup:
        """Построить клавиатуру для конкретного плана"""
        plan = InvestmentConfig.get_plan(plan_type)
        if not plan:
            return InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data='investments')
            ]])
        
        # Предустановленные суммы
        amounts = [plan.min_amount, plan.min_amount * 2, plan.min_amount * 5]
        keyboard = []
        
        # Кнопки с суммами
        for amount in amounts:
            if amount <= plan.max_amount:
                keyboard.append([
                    InlineKeyboardButton(
                        f"💰 Инвестировать {amount:,}₽",
                        callback_data=f'invest_amount_{plan_type}_{amount}'
                    )
                ])
        
        # Дополнительные кнопки
        keyboard.extend([
            [InlineKeyboardButton("✏️ Своя сумма", callback_data=f'invest_custom_{plan_type}')],
            [InlineKeyboardButton("🧮 Калькулятор", callback_data=f'calc_{plan_type}')],
            [InlineKeyboardButton("🔙 К планам", callback_data='investments')]
        ])
        
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def build_confirmation_keyboard(plan_type: str, amount: int) -> InlineKeyboardMarkup:
        """Построить клавиатуру подтверждения"""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f'confirm_invest_{plan_type}_{amount}'),
                InlineKeyboardButton("❌ Отмена", callback_data=f'invest_plan_{plan_type}')
            ]
        ])
    
    @staticmethod
    def build_back_keyboard(callback_data: str = 'investments') -> InlineKeyboardMarkup:
        """Построить клавиатуру с кнопкой назад"""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Назад", callback_data=callback_data)
        ]])

# Основные функции-обработчики
async def show_investments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать меню инвестиций с доступными планами"""
    try:
        text = InvestmentMessageBuilder.build_main_menu_text()
        keyboard = InvestmentKeyboardBuilder.build_main_menu_keyboard()
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                text=text,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Error in show_investments: {e}")
        await _send_error_message(update, "Ошибка при загрузке инвестиций")

async def handle_investment_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработать запрос по инвестициям"""
    query = update.callback_query
    await query.answer()
    
    try:
        data = query.data
        
        if data == 'invest_stats':
            await _handle_investment_stats(query)
        elif data.startswith('invest_plan_'):
            await _handle_plan_selection(query)
        elif data.startswith('invest_amount_'):
            await _handle_amount_selection(query)
        elif data.startswith('confirm_invest_'):
            await _handle_investment_confirmation(query)
        elif data.startswith('calc_'):
            await _handle_calculator(query)
        else:
            await _handle_unknown_request(query)
            
    except Exception as e:
        logger.error(f"Error in handle_investment_request: {e}")
        await _send_error_message(query, "Произошла ошибка при обработке запроса")

# Вспомогательные функции
async def _handle_investment_stats(query) -> None:
    """Обработать запрос статистики инвестиций"""
    service = InvestmentService()
    stats = service.get_user_investments_stats(query.from_user.id)
    
    text = InvestmentMessageBuilder.build_stats_text(stats)
    keyboard = InvestmentKeyboardBuilder.build_back_keyboard('investments')
    
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def _handle_plan_selection(query) -> None:
    """Обработать выбор инвестиционного плана"""
    plan_type = query.data.replace('invest_plan_', '')
    
    if plan_type not in InvestmentConfig.get_all_plans():
        await _handle_unknown_request(query)
        return
    
    text = InvestmentMessageBuilder.build_plan_details_text(plan_type)
    keyboard = InvestmentKeyboardBuilder.build_plan_keyboard(plan_type)
    
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def _handle_amount_selection(query) -> None:
    """Обработать выбор суммы инвестиции"""
    try:
        parts = query.data.split('_')
        plan_type = parts[2]
        amount = int(parts[3])
    except (IndexError, ValueError):
        await _send_error_message(query, "Неверные параметры")
        return
    
    # Показываем подтверждение
    plan = InvestmentConfig.get_plan(plan_type)
    if not plan:
        await _handle_unknown_request(query)
        return
    
    service = InvestmentService()
    profit_calc = service.calculate_profit(amount, plan_type)
    
    text = (
        f"💰 *ПОДТВЕРЖДЕНИЕ ИНВЕСТИЦИИ*\n\n"
        f"{plan.emoji} *План:* {plan.name}\n"
        f"💵 *Сумма:* {amount:,}₽\n"
        f"📈 *Ежедневно:* +{profit_calc['daily_profit']:,.2f}₽\n"
        f"🎯 *За месяц:* +{profit_calc['total_profit']:,.2f}₽\n"
        f"💎 *Итого:* {profit_calc['total_return']:,.2f}₽\n\n"
        f"❓ Подтверждаете инвестицию?"
    )
    
    keyboard = InvestmentKeyboardBuilder.build_confirmation_keyboard(plan_type, amount)
    
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def _handle_investment_confirmation(query) -> None:
    """Обработать подтверждение инвестиции"""
    try:
        parts = query.data.split('_')
        plan_type = parts[2]
        amount = int(parts[3])
    except (IndexError, ValueError):
        await _send_error_message(query, "Неверные параметры инвестиции")
        return
    
    service = InvestmentService()
    result = service.create_investment(query.from_user.id, plan_type, amount)
    
    if result['success']:
        text = InvestmentMessageBuilder.build_success_text(
            result['investment'], 
            result['plan'],
            result['expected_profit']
        )
        keyboard = InvestmentKeyboardBuilder.build_back_keyboard('investments')
    else:
        text = f"❌ *ОШИБКА*\n\n{result['error']}"
        keyboard = InvestmentKeyboardBuilder.build_back_keyboard(f'invest_plan_{plan_type}')
    
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def _handle_calculator(query) -> None:
    """Обработать запрос калькулятора"""
    plan_type = query.data.replace('calc_', '')
    plan = InvestmentConfig.get_plan(plan_type)
    
    if not plan:
        await _handle_unknown_request(query)
        return
    
    text = (
        f"🧮 *КАЛЬКУЛЯТОР ДОХОДНОСТИ*\n\n"
        f"{plan.emoji} *План:* {plan.name}\n"
        f"📈 *Ставка:* {plan.daily_profit * 100}% в день\n\n"
        f"💰 *ПРИМЕРЫ РАСЧЁТОВ:*\n"
    )
    
    # Примеры расчётов для разных сумм
    example_amounts = [plan.min_amount, plan.min_amount * 2, plan.min_amount * 5]
    service = InvestmentService()
    
    for amount in example_amounts:
        if amount <= plan.max_amount:
            calc = service.calculate_profit(amount, plan_type)
            text += (
                f"\n📊 {amount:,}₽:\n"
                f"├ День: +{calc['daily_profit']:,.0f}₽\n"
                f"├ Месяц: +{calc['total_profit']:,.0f}₽\n"
                f"└ Итого: {calc['total_return']:,.0f}₽"
            )
    
    keyboard = InvestmentKeyboardBuilder.build_back_keyboard(f'invest_plan_{plan_type}')
    
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def _handle_unknown_request(query) -> None:
    """Обработать неизвестный запрос"""
    await query.edit_message_text(
        text="🚧 *Функция в разработке*\n\nЭтот раздел скоро будет доступен!",
        reply_markup=InvestmentKeyboardBuilder.build_back_keyboard('investments'),
        parse_mode='Markdown'
    )

async def _send_error_message(update_or_query, error_text: str) -> None:
    """Отправить сообщение об ошибке"""
    keyboard = InvestmentKeyboardBuilder.build_back_keyboard('investments')
    text = f"❌ *ОШИБКА*\n\n{error_text}"
    
    if hasattr(update_or_query, 'callback_query'):
        await update_or_query.callback_query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    else:
        await update_or_query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )