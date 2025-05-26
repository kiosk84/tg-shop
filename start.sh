#!/bin/bash

# Загружаем переменные окружения из .env файла, если он существует
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Запускаем бота
python bot.py
