"""
User — User accounts with authentication and subscription plan.
"""

from datetime import datetime
from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan: Mapped[str] = mapped_column(
        String(20), default="free"  # free / starter / pro / advisor
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # ── Multi-Step SEBI Compliance & Registration Fields ──
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    otp_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dob: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    
    # ── Investor Profile ──
    risk_appetite: Mapped[str | None] = mapped_column(String(50), nullable=True)
    experience: Mapped[str | None] = mapped_column(String(50), nullable=True)
    goal: Mapped[str | None] = mapped_column(String(100), nullable=True)
    horizon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    
    # ── Legal & Mandate ──
    disclaimer_accepted: Mapped[bool] = mapped_column(Boolean, default=False)


    def __repr__(self) -> str:
        return f"<User {self.email} plan={self.plan}>"
