from datetime import datetime
from typing import Union
import re

def format_currency(amount: Union[int, float]) -> str:
    """Форматирование суммы в красивый вид"""
    return f"{amount:,.2f}₽".replace(",", " ").replace(".00", "")

def format_datetime(dt: datetime) -> str:
    """Форматирование даты и времени"""
    return dt.strftime("%d.%m.%Y %H:%M")

def validate_amount(amount_str: str) -> tuple[bool, float, str]:
    """Проверка корректности суммы"""
    try:
        # Удаляем все символы кроме цифр и точки
        cleaned = re.sub(r'[^\d.]', '', amount_str)
        amount = float(cleaned)
        if amount <= 0:
            return False, 0, "❌ Сумма должна быть больше нуля"
        return True, amount, ""
    except ValueError:
        return False, 0, "❌ Неверный формат суммы"

def validate_payment_details(method: str, details: str) -> tuple[bool, str]:
    """Проверка корректности реквизитов"""
    details = details.strip()
    
    if not details:
        return False, "❌ Реквизиты не могут быть пустыми"
    
    if method == "card":
        # Проверка номера карты (должно быть 16-19 цифр)
        card_number = re.sub(r'\D', '', details)
        if not (16 <= len(card_number) <= 19):
            return False, "❌ Неверный формат номера карты"
    
    elif method == "qiwi":
        # Проверка номера телефона
        phone = re.sub(r'\D', '', details)
        if not (len(phone) == 11 and phone.startswith(('7', '8'))):
            return False, "❌ Неверный формат номера телефона"
    
    elif method == "ymoney":
        # Проверка номера кошелька
        wallet = re.sub(r'\D', '', details)
        if not (len(wallet) == 15 and wallet.startswith('4100')):
            return False, "❌ Неверный формат номера кошелька"
    
    return True, ""

def get_time_until(target: datetime) -> str:
    """Получение времени до указанной даты в красивом формате"""
    now = datetime.now()
    diff = target - now
    
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60
    
    if diff.days > 0:
        return f"{diff.days}д {hours}ч {minutes}м"
    elif hours > 0:
        return f"{hours}ч {minutes}м"
    else:
        return f"{minutes}м"

def plural_form(n: int, forms: tuple[str, str, str]) -> str:
    """Возвращает слово в нужной форме в зависимости от числа
    
    forms = (одна штука, две штуки, пять штук)
    """
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    elif 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return forms[1]
    else:
        return forms[2]