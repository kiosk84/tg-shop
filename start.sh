#!/bin/bash

# Загружаем переменные окружения из .env файла, если он существует
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Устанавливаем порт по умолчанию, если не задан
export PORT=${PORT:-4000}

echo "Starting bot on port $PORT"

# Запускаем бота с exec для правильной передачи сигналов
exec python bot.py
