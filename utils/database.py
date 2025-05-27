import os
import json
import logging
import shutil
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Set
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from config import DATABASE_URL, DATABASE_BACKUP_DIR, DATABASE_BACKUP_INTERVAL, DATA_FILE, BLOCKED_USERS_FILE
from models.user import Base, User, Referral, Investment, WithdrawalRequest

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
        
        # Создаем все таблицы
        Base.metadata.create_all(self.engine)
        
        # Создаем директорию для бэкапов
        os.makedirs(DATABASE_BACKUP_DIR, exist_ok=True)

        self._users: Dict[int, User] = {}
        self._blocked_users: Set[int] = set()
        self.load_data()

    def load_data(self) -> None:
        """Загружает данные пользователей и список заблокированных"""
        # Загрузка пользователей
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for user_id_str, user_data in data.items():
                        user_id = int(user_id_str)
                        self._users[user_id] = User.from_dict(user_id, user_data)
        except Exception as e:
            logger.error(f"Ошибка загрузки данных пользователей: {e}")
            self._users = {}

        # Загрузка заблокированных пользователей
        try:
            if os.path.exists(BLOCKED_USERS_FILE):
                with open(BLOCKED_USERS_FILE, 'r', encoding='utf-8') as f:
                    self._blocked_users = set(json.load(f))
        except Exception as e:
            logger.error(f"Ошибка загрузки списка заблокированных: {e}")
            self._blocked_users = set()

    def save_data(self) -> None:
        """Сохраняет данные пользователей и список заблокированных"""
        # Сохранение пользователей
        try:
            data = {str(uid): user.to_dict() for uid, user in self._users.items()}
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"Ошибка сохранения данных пользователей: {e}")

        # Сохранение заблокированных пользователей
        try:
            with open(BLOCKED_USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(list(self._blocked_users), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения списка заблокированных: {e}")

    def get_session(self) -> Session:
        """Получение сессии базы данных"""
        return self.SessionLocal()

    def backup_database(self) -> bool:
        """Создание резервной копии базы данных"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(DATABASE_BACKUP_DIR, f'shop_{timestamp}.db')
            shutil.copy2('shop.db', backup_path)
            
            # Удаляем старые бэкапы
            self._cleanup_old_backups()
            return True
        except Exception as e:
            logger.error(f"Ошибка создания бэкапа: {e}")
            return False

    def _cleanup_old_backups(self):
        """Удаление старых резервных копий"""
        try:
            files = sorted(os.listdir(DATABASE_BACKUP_DIR))
            while len(files) > 5:  # Оставляем только 5 последних бэкапов
                os.remove(os.path.join(DATABASE_BACKUP_DIR, files[0]))
                files.pop(0)
        except Exception as e:
            logger.error(f"Ошибка при очистке старых бэкапов: {e}")

    def get_user(self, user_id: int) -> Optional[User]:
        """Получение пользователя по ID"""
        with self.get_session() as session:
            return session.query(User).filter(User.user_id == user_id).first()

    def create_user(self, user_id: int) -> User:
        """Создание нового пользователя"""
        with self.get_session() as session:
            user = User(user_id=user_id)
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    def get_or_create_user(self, user_id: int) -> User:
        """Получение существующего пользователя или создание нового"""
        user = self.get_user(user_id)
        if user is None:
            user = self.create_user(user_id)
        return user

    # Методы для работы с инвестициями
    def create_investment(self, user_id: int, plan_id: str, amount: float) -> Optional[Investment]:
        """Создание новой инвестиции"""
        with self.get_session() as session:
            try:
                user = session.query(User).filter(User.user_id == user_id).first()
                if user and user.balance >= amount:
                    investment = Investment(
                        user_id=user.id,
                        plan_id=plan_id,
                        amount=amount
                    )
                    user.balance -= amount
                    session.add(investment)
                    session.commit()
                    return investment
            except SQLAlchemyError as e:
                logger.error(f"Ошибка создания инвестиции: {e}")
                session.rollback()
            return None

    def calculate_investments_profit(self):
        """Расчет прибыли по всем активным инвестициям"""
        with self.get_session() as session:
            try:
                investments = session.query(Investment).filter(
                    Investment.status == 'active'
                ).all()

                for inv in investments:
                    profit = inv.calculate_profit()
                    if profit > 0:
                        inv.total_profit += profit
                        inv.last_profit_date = datetime.now()
                        inv.user.balance += profit
                        inv.user.total_earned += profit

                session.commit()
            except SQLAlchemyError as e:
                logger.error(f"Ошибка при расчете прибыли: {e}")
                session.rollback()

    # Методы для работы с выводом средств
    def create_withdrawal_request(
        self, user_id: int, amount: float, method: str, details: str
    ) -> Optional[WithdrawalRequest]:
        """Создание заявки на вывод средств"""
        with self.get_session() as session:
            try:
                user = session.query(User).filter(User.user_id == user_id).first()
                if user and user.balance >= amount:
                    request = WithdrawalRequest(
                        user_id=user.id,
                        amount=amount,
                        method=method,
                        details=details
                    )
                    user.balance -= amount
                    session.add(request)
                    session.commit()
                    return request
            except SQLAlchemyError as e:
                logger.error(f"Ошибка создания заявки на вывод: {e}")
                session.rollback()
            return None

    def process_withdrawal_request(
        self, request_id: int, approved: bool, admin_id: int
    ) -> bool:
        """Обработка заявки на вывод средств"""
        with self.get_session() as session:
            try:
                request = session.query(WithdrawalRequest).get(request_id)
                if request:
                    if not approved:
                        request.user.balance += request.amount
                    request.status = 'approved' if approved else 'rejected'
                    request.processed_date = datetime.now()
                    request.processed_by = admin_id
                    session.commit()
                    return True
            except SQLAlchemyError as e:
                logger.error(f"Ошибка обработки заявки на вывод: {e}")
                session.rollback()
            return False

    # Методы для аналитики
    def get_statistics(self) -> Dict:
        """Получение общей статистики"""
        with self.get_session() as session:
            try:
                stats = {
                    'total_users': session.query(User).count(),
                    'active_users': session.query(User).filter(User.channel_joined == True).count(),
                    'total_balance': session.query(func.sum(User.balance)).scalar() or 0,
                    'total_earned': session.query(func.sum(User.total_earned)).scalar() or 0,
                    'total_withdrawals': session.query(func.sum(User.withdrawals)).scalar() or 0,
                    'total_investments': session.query(func.sum(Investment.amount)).scalar() or 0,
                    'total_investment_profit': session.query(func.sum(Investment.total_profit)).scalar() or 0
                }
                return stats
            except SQLAlchemyError as e:
                logger.error(f"Ошибка получения статистики: {e}")
                return {}

    def add_referral(self, referrer_id: int, referred_id: int) -> bool:
        """Добавление реферала"""
        with self.get_session() as session:
            try:
                # Проверяем существование пользователей
                referrer = session.query(User).filter(User.user_id == referrer_id).first()
                referred = session.query(User).filter(User.user_id == referred_id).first()
                
                if not referrer or not referred:
                    return False
                
                # Проверяем, не является ли уже рефералом
                existing = session.query(Referral).filter(
                    Referral.referrer_id == referrer.id,
                    Referral.referred_id == referred.id
                ).first()
                
                if existing:
                    return False
                
                # Создаем связь
                referral = Referral(
                    referrer_id=referrer.id,
                    referred_id=referred.id,
                    date=datetime.now()
                )
                session.add(referral)
                session.commit()
                return True
            except Exception as e:
                logger.error(f"Ошибка добавления реферала: {e}")
                session.rollback()
                return False

    def get_user_referrals(self, user_id: int) -> List[User]:
        """Получение списка рефералов пользователя"""
        with self.get_session() as session:
            user = session.query(User).filter(User.user_id == user_id).first()
            if user:
                return [ref.referred for ref in user.referrals]
            return []

    def update_user_balance(self, user_id: int, amount: float, is_add: bool = True) -> bool:
        """Обновление баланса пользователя"""
        with self.get_session() as session:
            try:
                user = session.query(User).filter(User.user_id == user_id).first()
                if not user:
                    return False
                
                if is_add:
                    user.balance += amount
                    user.total_earned += amount
                else:
                    if user.balance < amount:
                        return False
                    user.balance -= amount
                
                session.commit()
                return True
            except Exception as e:
                logger.error(f"Ошибка обновления баланса: {e}")
                session.rollback()
                return False

    def get_user_statistics(self, user_id: int) -> Dict:
        """Получение статистики пользователя"""
        with self.get_session() as session:
            user = session.query(User).filter(User.user_id == user_id).first()
            if not user:
                return {}
            
            stats = {
                'balance': user.balance,
                'total_earned': user.total_earned,
                'withdrawals': user.withdrawals,
                'referrals_count': len(user.referrals),
                'investments_count': len(user.investments),
                'total_invested': sum(inv.amount for inv in user.investments),
                'investment_profit': sum(inv.total_profit for inv in user.investments),
            }
            return stats

    def get_global_statistics(self) -> Dict:
        """Получение общей статистики бота"""
        with self.get_session() as session:
            total_users = session.query(func.count(User.id)).scalar()
            total_balance = session.query(func.sum(User.balance)).scalar() or 0
            total_earned = session.query(func.sum(User.total_earned)).scalar() or 0
            total_withdrawals = session.query(func.sum(User.withdrawals)).scalar() or 0
            total_investments = session.query(func.sum(Investment.amount)).scalar() or 0
            total_profit = session.query(func.sum(Investment.total_profit)).scalar() or 0
            
            return {
                'total_users': total_users,
                'total_balance': total_balance,
                'total_earned': total_earned,
                'total_withdrawals': total_withdrawals,
                'total_investments': total_investments,
                'total_profit': total_profit
            }