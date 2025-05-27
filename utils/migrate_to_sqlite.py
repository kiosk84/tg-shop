import json
import logging
from datetime import datetime
from config import DATA_FILE, BLOCKED_USERS_FILE
from models.user import User, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_data():
    """Миграция данных из JSON файлов в SQLite"""
    try:
        # Создаем подключение к базе данных
        engine = create_engine('sqlite:///shop.db')
        Session = sessionmaker(bind=engine)
        session = Session()

        # Загрузка старых данных
        users_data = {}
        blocked_users = set()

        # Загрузка данных пользователей
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                users_data = {int(k): v for k, v in data.items()}
            logger.info(f"Загружено {len(users_data)} пользователей из JSON")
        except FileNotFoundError:
            logger.warning("Файл с данными пользователей не найден")
        except json.JSONDecodeError:
            logger.error("Ошибка при чтении файла пользователей")

        # Загрузка списка заблокированных пользователей
        try:
            with open(BLOCKED_USERS_FILE, 'r', encoding='utf-8') as f:
                blocked_users = set(json.load(f))
            logger.info(f"Загружено {len(blocked_users)} заблокированных пользователей")
        except FileNotFoundError:
            logger.warning("Файл заблокированных пользователей не найден")
        except json.JSONDecodeError:
            logger.error("Ошибка при чтении файла блокировок")

        # Миграция данных
        for user_id, user_data in users_data.items():
            try:
                user = User(
                    user_id=user_id,
                    balance=float(user_data.get('balance', 0)),
                    total_earned=float(user_data.get('total_earned', 0)),
                    withdrawals=float(user_data.get('withdrawals', 0)),
                    last_bonus=datetime.fromisoformat(user_data.get('last_bonus', datetime.min.isoformat())),
                    join_date=datetime.fromisoformat(user_data.get('join_date', datetime.now().isoformat())),
                    channel_joined=bool(user_data.get('channel_joined', False)),
                    is_blocked=user_id in blocked_users
                )
                session.add(user)
                logger.info(f"Мигрирован пользователь {user_id}")
            except Exception as e:
                logger.error(f"Ошибка миграции пользователя {user_id}: {e}")

        # Сохраняем изменения
        session.commit()
        logger.info("Миграция успешно завершена")

    except Exception as e:
        logger.error(f"Ошибка миграции: {e}")
        if session:
            session.rollback()
    finally:
        if session:
            session.close()

if __name__ == '__main__':
    migrate_data()
