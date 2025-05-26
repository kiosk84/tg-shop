# 🤖 Telegram Shop Bot

Бот для заработка в Telegram с реферальной системой и выводом средств.

## 🚀 Основные функции

- 💰 Реферальная система с бонусами за приглашения
- 🎁 Ежедневные бонусы
- 💸 Вывод средств (несколько платежных систем)
- 👑 Админ-панель с полной статистикой
- 📊 Система уровней пользователей
- 📢 Обязательная подписка на канал

## 🛠 Установка

1. Клонируйте репозиторий:
```bash
git clone https://github.com/YOUR_USERNAME/tg-shop.git
cd tg-shop
```

2. Установите зависимости:
```bash
pip install -r requirements.txt
```

3. Создайте файл .env и заполните его:
```env
BOT_TOKEN=your_bot_token
CHANNEL_ID=your_channel_id
CHANNEL_LINK=https://t.me/your_channel
CHANNEL_NAME=Your Channel Name
ADMIN_IDS=123456789,987654321
MIN_WITHDRAW=100
DAILY_BONUS=2
REFERRAL_BONUS=5
```

4. Запустите бота:
```bash
python bot.py
```

## 🔧 Настройка systemd

Создайте файл `/etc/systemd/system/tgshop.service`:
```ini
[Unit]
Description=Telegram Shop Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Активируйте сервис:
```bash
sudo systemctl daemon-reload
sudo systemctl enable tgshop
sudo systemctl start tgshop
```

## 📦 Деплой на Render.com

1. Создайте новый Web Service на Render.com
2. Подключите ваш GitHub репозиторий
3. Настройте следующие параметры:
   - Name: `vibe-tg-bot` (или ваше название)
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `./start.sh`
   
4. Добавьте следующие переменные окружения:
   ```
   RENDER=true
   RENDER_EXTERNAL_URL=https://ваш-домен.onrender.com
   PORT=3000
   BOT_TOKEN=ваш_токен_бота
   CHANNEL_ID=ваш_id_канала
   CHANNEL_LINK=ваша_ссылка_на_канал
   CHANNEL_NAME=название_канала
   ANALYTICS_CHAT_ID=id_чата_аналитики
   ADMIN_IDS=id_админов
   MIN_WITHDRAW=100
   ```

5. Нажмите "Create Web Service"

После деплоя бот автоматически настроит вебхук и начнет работать в режиме webhook.

## 🔍 Мониторинг на Render.com

- Логи доступны в разделе "Logs"
- Статус бота можно отслеживать в разделе "Events"
- Метрики использования в разделе "Metrics"

## 📝 Логи

Логи бота сохраняются в файл `bot.log`. Для просмотра логов systemd:
```bash
sudo journalctl -u tgshop -f
```

## 🔒 Безопасность

- Все конфиденциальные данные хранятся в .env файле
- Пароли и токены не публикуются в репозитории
- Используется безопасное хранение данных пользователей

## 📄 Лицензия

MIT License