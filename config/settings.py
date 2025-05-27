import os
from dotenv import load_dotenv

# Загрузка конфигурации из .env файла
load_dotenv()

# 🔐 Основные настройки бота
TOKEN = os.getenv('BOT_TOKEN')

# 💰 Финансовые настройки
MIN_WITHDRAW = int(os.getenv('MIN_WITHDRAW', 50))
DAILY_BONUS = int(os.getenv('DAILY_BONUS', 2))
REFERRAL_BONUS = int(os.getenv('REFERRAL_BONUS', 5))

# 📈 Инвестиционные планы
INVESTMENT_PLANS = {
    'basic': {
        'name': '🥉 Базовый',
        'min_amount': 100,
        'daily_profit': 0.01,  # 1% в день
        'description': 'Начальный план для новых инвесторов'
    },
    'advanced': {
        'name': '🥈 Продвинутый',
        'min_amount': 500,
        'daily_profit': 0.015,  # 1.5% в день
        'description': 'Оптимальный план для активных инвесторов'
    },
    'vip': {
        'name': '🥇 VIP',
        'min_amount': 1000,
        'daily_profit': 0.02,  # 2% в день
        'description': 'Максимальная доходность для крупных инвесторов'
    }
}

# 👑 Администраторы
ADMIN_IDS = [int(id_) for id_ in os.getenv('ADMIN_IDS', '').split(',') if id_]

# 📢 Настройки канала
CHANNEL_ID = os.getenv('CHANNEL_ID')
CHANNEL_LINK = os.getenv('CHANNEL_LINK')
CHANNEL_NAME = os.getenv('CHANNEL_NAME')

# 📊 Аналитика
ANALYTICS_CHAT_ID = os.getenv('ANALYTICS_CHAT_ID')

# 🌐 Настройки веб-сервера
WEBHOOK_ENABLED = bool(os.getenv('RENDER'))
PORT = int(os.getenv('PORT', 3000))
WEBHOOK_URL = f"{os.getenv('RENDER_EXTERNAL_URL')}/{TOKEN}" if WEBHOOK_ENABLED else None

# 🗄️ Настройки базы данных
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///shop.db')
DATABASE_BACKUP_DIR = 'backups'
DATABASE_BACKUP_INTERVAL = 24  # часов

# Настройки Cron сервера для render.com
RENDER_APP_URL = os.getenv('RENDER_APP_URL', 'https://your-app-name.onrender.com')
CRON_INTERVAL = int(os.getenv('CRON_INTERVAL', 600))  # 10 минут по умолчанию