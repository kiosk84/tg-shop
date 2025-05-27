import os
import logging
from datetime import datetime
from typing import List, Optional, Dict, AsyncGenerator
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from config import DATABASE_URL, DATABASE_BACKUP_DIR
from models.user import Base, User, Referral, Investment, WithdrawalRequest
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

class Database:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Инициализация подключения к базе данных"""
        self.engine = create_engine(DATABASE_URL)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.session = self.SessionLocal()
        
        # Создаем все таблицы
        Base.metadata.create_all(self.engine)
        
        # Создаем директорию для бэкапов
        os.makedirs(DATABASE_BACKUP_DIR, exist_ok=True)

    def get_user(self, user_id: int) -> Optional[User]:
        """Получить пользователя по ID"""
        return self.session.query(User).filter(User.user_id == user_id).first()

    def create_user(self, user_id: int) -> User:
        """Создать нового пользователя"""
        user = User(user_id=user_id)
        self.session.add(user)
        self.session.commit()
        return user

    def get_referral(self, referrer_id: int, referred_id: int) -> Optional[Referral]:
        """Получить реферальную связь"""
        referrer = self.get_user(referrer_id)
        referred = self.get_user(referred_id)
        if not referrer or not referred:
            logger.warning(f"Не удалось получить реферал: referrer={referrer_id}, referred={referred_id}")
            return None
        return self.session.query(Referral)\
            .filter(Referral.referrer_id == referrer.id, Referral.referred_id == referred.id)\
            .first()

    def get_user_referrals(self, user_id: int) -> list:
        """Получить список рефералов пользователя"""
        user = self.get_user(user_id)
        if not user:
            logger.warning(f"Пользователь не найден при запросе рефералов: user_id={user_id}")
            return []
        return user.referrals

    def create_referral(self, referrer_id: int, referred_id: int, bonus: float = 0) -> Optional[Referral]:
        """Создать реферальную связь"""
        referrer = self.get_user(referrer_id)
        referred = self.get_user(referred_id)
        if not referrer or not referred:
            logger.warning(f"Не удалось создать реферал: referrer={referrer_id}, referred={referred_id}")
            return None
        referral = Referral(
            referrer_id=referrer.id,
            referred_id=referred.id,
            bonus_paid=bonus
        )
        self.session.add(referral)
        self.session.commit()
        return referral

    def get_user_statistics(self) -> Dict:
        """Получить статистику пользователей"""
        total_users = self.session.query(func.count(User.id)).scalar()
        active_users = self.session.query(func.count(User.id))\
            .filter(User.channel_joined == True)\
            .scalar()
        blocked_users = self.session.query(func.count(User.id))\
            .filter(User.is_blocked == True)\
            .scalar()
            
        return {
            'total_users': total_users,
            'active_users': active_users,
            'blocked_users': blocked_users
        }

    def get_all_users(self) -> List[User]:
        """Получить всех пользователей"""
        return self.session.query(User).all()

    def get_investments_statistics(self) -> Dict:
        """Получить статистику инвестиций"""
        total_investments = self.session.query(func.sum(Investment.amount)).scalar() or 0
        total_profit_paid = self.session.query(func.sum(Investment.total_profit)).scalar() or 0
        active_investments = self.session.query(func.count(Investment.id)).scalar()
            
        return {
            'total_investments': total_investments,
            'total_profit_paid': total_profit_paid,
            'active_investments': active_investments
        }

    def backup_database(self):
        """Создать резервную копию базы данных"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(DATABASE_BACKUP_DIR, f'backup_{timestamp}.sql')
            
            # TODO: Implement actual database backup logic
            logger.info(f"База данных успешно сохранена в {backup_path}")
        except Exception as e:
            logger.error(f"Ошибка при создании резервной копии: {e}")

    def create_withdrawal_request(self, user_id: int, amount: float, method: str, details: str) -> Optional[WithdrawalRequest]:
        """Создать заявку на вывод средств"""
        user = self.get_user(user_id)
        if not user:
            logger.warning(f"Не удалось создать заявку на вывод: user_id={user_id} не найден")
            return None
        withdrawal = WithdrawalRequest(
            user_id=user.id,
            amount=amount,
            method=method,
            details=details,
            date=datetime.now(),
            status='pending'
        )
        user.balance -= amount
        self.session.add(withdrawal)
        self.session.commit()
        return withdrawal

    @asynccontextmanager
    async def get_session() -> AsyncGenerator[Session, None]:
        """Асинхронный контекстный менеджер для работы с сессией базы данных"""
        db = Database()
        session = db.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()