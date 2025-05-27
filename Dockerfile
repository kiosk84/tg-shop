FROM python:3.10-slim

WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы
COPY . .

# Делаем start.sh исполняемым
RUN chmod +x start.sh

# Указываем порт, который будет прослушивать приложение
EXPOSE 3000

# Запускаем приложение через скрипт
CMD ["./start.sh"]
