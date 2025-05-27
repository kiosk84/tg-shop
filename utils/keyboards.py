from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from typing import List, Optional
from config import CHANNEL_LINK, CHANNEL_NAME, MIN_WITHDRAW, INVESTMENT_PLANS

class Keyboards:
    @staticmethod
    def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
        """Главное меню бота"""
        keyboard = [
            [
                InlineKeyboardButton("💰 Баланс", callback_data='balance'),
                InlineKeyboardButton("📊 Статистика", callback_data='stats')
            ],
            [
                InlineKeyboardButton("📈 Инвестировать", callback_data='investments'),
                InlineKeyboardButton("👥 Рефералы", callback_data='referral')
            ],
            [
                InlineKeyboardButton("💸 Вывести средства", callback_data='withdraw'),
                InlineKeyboardButton("ℹ️ Как заработать", callback_data='info')
            ],
            [
                InlineKeyboardButton("🏆 Топ пользователей", callback_data='top'),
                InlineKeyboardButton("📢 Наш канал", url=CHANNEL_LINK)
            ]
        ]
        
        if is_admin:
            keyboard.append([InlineKeyboardButton("👑 АДМИН ПАНЕЛЬ", callback_data='admin_panel')])
        
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def admin_panel() -> InlineKeyboardMarkup:
        """Клавиатура админ-панели"""
        keyboard = [
            [
                InlineKeyboardButton("📊 Статистика бота", callback_data='admin_stats'),
                InlineKeyboardButton("👥 Управление", callback_data='admin_users')
            ],
            [
                InlineKeyboardButton("📢 Рассылка всем", callback_data='admin_broadcast'),
                InlineKeyboardButton("💬 Отправить пользователю", callback_data='admin_send_user')
            ],
            [
                InlineKeyboardButton("🚫 Заблокировать", callback_data='admin_block'),
                InlineKeyboardButton("✅ Разблокировать", callback_data='admin_unblock')
            ],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def channel_check() -> InlineKeyboardMarkup:
        """Клавиатура проверки подписки на канал"""
        keyboard = [
            [InlineKeyboardButton("📢 Подписаться на канал", url=CHANNEL_LINK)],
            [InlineKeyboardButton("✅ Проверить подписку", callback_data='check_subscription')]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def investment_menu() -> InlineKeyboardMarkup:
        """Меню инвестиций"""
        keyboard = []
        for plan_id, plan in INVESTMENT_PLANS.items():
            keyboard.append([
                InlineKeyboardButton(
                    f"{plan['name']} от {plan['min_amount']}₽ ({int(plan['daily_profit']*100)}%)",
                    callback_data=f'invest_{plan_id}'
                )
            ])
        
        keyboard.extend([
            [InlineKeyboardButton("📊 Мои инвестиции", callback_data='my_investments')],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')]
        ])
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def withdrawal_menu(balance: float) -> InlineKeyboardMarkup:
        """Меню вывода средств"""
        keyboard = []
        if balance >= MIN_WITHDRAW:
            keyboard.extend([
                [InlineKeyboardButton(f"💸 Вывести {MIN_WITHDRAW}₽", 
                                    callback_data=f"confirm_withdraw_{MIN_WITHDRAW}")],
                [InlineKeyboardButton(f"💰 Вывести всё ({balance}₽)", 
                                    callback_data=f"confirm_withdraw_{balance}")],
                [InlineKeyboardButton("📋 История выводов", callback_data='history')]
            ])
        else:
            keyboard.extend([
                [InlineKeyboardButton("👥 Пригласить друзей", callback_data='referral')],
                [InlineKeyboardButton("🎁 Получить бонус", callback_data='bonus')]
            ])
        
        keyboard.append([InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')])
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def payment_methods(amount: float) -> InlineKeyboardMarkup:
        """Выбор способа оплаты"""
        keyboard = [
            [InlineKeyboardButton("💳 Банковская карта", callback_data=f'payment_card_{amount}')],
            [InlineKeyboardButton("💰 QIWI", callback_data=f'payment_qiwi_{amount}')],
            [InlineKeyboardButton("📱 ЮMoney", callback_data=f'payment_ymoney_{amount}')],
            [InlineKeyboardButton("❌ Отмена", callback_data='withdraw')]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def back_to_menu() -> InlineKeyboardMarkup:
        """Кнопка возврата в главное меню"""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')
        ]])

    @staticmethod
    def admin_action_withdraw(withdrawal_id: str) -> InlineKeyboardMarkup:
        """Кнопки действий с заявкой на вывод"""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{withdrawal_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{withdrawal_id}")
        ]])

    @staticmethod
    def back_to_admin() -> InlineKeyboardMarkup:
        """Кнопка возврата в админ-панель"""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Вернуться в админ-панель", callback_data='admin_panel')
        ]])

    @staticmethod
    def cancel_action(return_to: str) -> InlineKeyboardMarkup:
        """Кнопка отмены действия"""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data=return_to)
        ]])

    @staticmethod
    def add_back_button(keyboard_buttons: list) -> InlineKeyboardMarkup:
        """Добавляет кнопку возврата в меню к существующей клавиатуре"""
        keyboard_buttons.append([
            InlineKeyboardButton("⬅️ Главное меню", callback_data='menu')
        ])
        return InlineKeyboardMarkup(keyboard_buttons)