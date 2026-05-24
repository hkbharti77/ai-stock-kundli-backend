"""
Company — Master table for all NSE/BSE listed companies.
"""

from datetime import datetime
from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sub_sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(10), nullable=True)  # NSE / BSE
    market_cap: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    financials = relationship("Financial", back_populates="company", lazy="selectin")
    price_history = relationship("PriceHistory", back_populates="company", lazy="selectin")
    agent_outputs = relationship("AgentOutput", back_populates="company", lazy="selectin", cascade="all, delete-orphan")
    news_articles = relationship("NewsArticle", back_populates="company", lazy="selectin", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Company {self.ticker} — {self.name}>"
