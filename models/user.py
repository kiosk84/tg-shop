from datetime import datetime
from typing import List, Optional
from sqlalchemy import Column, Integer, Float, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship, Mapped
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, nullable=False)
    balance = Column(Float, default=0)
    total_earned = Column(Float, default=0)
    withdrawals = Column(Float, default=0)
    last_bonus = Column(DateTime, default=datetime.min)
    join_date = Column(DateTime, default=datetime.now)
    channel_joined = Column(Boolean, default=False)
    is_blocked = Column(Boolean, default=False)
    
    # Связи с другими таблицами
    referrals = relationship("Referral", back_populates="referrer", foreign_keys="[Referral.referrer_id]")
    referred_by = relationship("Referral", back_populates="referred", foreign_keys="[Referral.referred_id]", uselist=False)
    investments = relationship("Investment", back_populates="user")
    withdrawal_requests = relationship("WithdrawalRequest", back_populates="user")

    def __repr__(self):
        return f"<User(user_id={self.user_id}, balance={self.balance})>"

class Referral(Base):
    __tablename__ = 'referrals'

    id = Column(Integer, primary_key=True)
    referrer_id = Column(Integer, ForeignKey('users.id'))
    referred_id = Column(Integer, ForeignKey('users.id'))
    date = Column(DateTime, default=datetime.now)
    bonus_paid = Column(Float)

    referrer = relationship("User", back_populates="referrals", foreign_keys=[referrer_id])
    referred = relationship("User", back_populates="referred_by", foreign_keys=[referred_id])

class Investment(Base):
    __tablename__ = 'investments'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    plan_type = Column(String)
    amount = Column(Float)
    daily_profit = Column(Float)
    current_profit = Column(Float, default=0)
    start_date = Column(DateTime, default=datetime.now)
    end_date = Column(DateTime)
    last_profit_date = Column(DateTime, default=datetime.now)
    is_finished = Column(Boolean, default=False)

    user = relationship("User", back_populates="investments")

    def calculate_profit(self) -> float:
        """Рассчитывает прибыль по инвестиции"""
        now = datetime.now()
        days = (now - self.last_profit_date).days
        if days > 0:
            daily_profit = self.amount * self.daily_profit
            return daily_profit * days
        return 0

class WithdrawalRequest(Base):
    __tablename__ = 'withdrawal_requests'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    amount = Column(Float)
    method = Column(String)
    details = Column(String)
    status = Column(String, default='pending')  # pending, approved, rejected
    date = Column(DateTime, default=datetime.now)
    processed_date = Column(DateTime, nullable=True)
    processed_by = Column(Integer, nullable=True)  # admin_id

    user = relationship("User", back_populates="withdrawal_requests")