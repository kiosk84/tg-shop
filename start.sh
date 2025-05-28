#!/bin/bash

# Загружаем переменные окружения из .env файла, если он существует
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Устанавливаем порт по умолчанию, если не задан
export PORT=${PORT:-3000}

echo "Starting bot on port $PORT"

# Запускаем бота
exec python bot.py
