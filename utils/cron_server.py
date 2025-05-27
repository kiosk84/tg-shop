import asyncio
import aiohttp
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

class CronServer:
    def __init__(self, app_url: str, interval: int = 600):
        """
        Инициализация сервера для поддержания работы на render.com
        
        :param app_url: URL вашего приложения на render.com
        :param interval: Интервал между пингами в секундах (по умолчанию 600 сек = 10 минут)
        """
        self.app_url = app_url
        self.interval = interval
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        
    async def ping(self) -> bool:
        """Отправка пинга на URL приложения"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.app_url) as response:
                    if response.status == 200:
                        logger.info(f"[{datetime.now()}] Успешный пинг")
                        return True
                    else:
                        logger.warning(f"[{datetime.now()}] Пинг вернул статус {response.status}")
                        return False
        except Exception as e:
            logger.error(f"[{datetime.now()}] Ошибка при пинге: {str(e)}")
            return False

    async def _ping_loop(self):
        """Основной цикл пингов"""
        while self.is_running:
            await self.ping()
            await asyncio.sleep(self.interval)

    def start(self):
        """Запуск сервера пингов"""
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._ping_loop())
            logger.info("Сервер пингов запущен")

    def stop(self):
        """Остановка сервера пингов"""
        if self.is_running:
            self.is_running = False
            if self._task:
                self._task.cancel()
            logger.info("Сервер пингов остановлен")
