"""
SentimentScore — Database model to track rolling historical daily sentiment scores.
"""

from datetime import date, datetime
from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Float,
    Date,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class SentimentScore(Base):
    __tablename__ = "sentiment_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # -100.0 to +100.0
    management_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    news_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    market_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=80.0)  # 0.0 to 100.0
    classification: Mapped[str] = mapped_column(String(30), nullable=False, default="stable", index=True)  # improving, stable, deteriorating

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    company = relationship("Company", back_populates="sentiment_scores")

    def __repr__(self) -> str:
        return f"<SentimentScore {self.date} - Score: {self.score} Classification: {self.classification}>"
