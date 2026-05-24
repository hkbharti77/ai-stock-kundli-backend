"""
NewsArticle — Database model for company news articles, classification, sentiment, and risk tags.
"""

from datetime import datetime
from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. Economic Times, Mint, BSE, NSE, SEBI
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    
    # AI Classifications & Scoring
    classification: Mapped[str] = mapped_column(String(50), nullable=False, default="Neutral — Informational", index=True)
    impact_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 1 to 10
    sentiment: Mapped[str] = mapped_column(String(20), nullable=False, default="neutral", index=True)  # positive, negative, neutral
    risk_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)  # JSON array of strings
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    company = relationship("Company", back_populates="news_articles")

    def __repr__(self) -> str:
        return f"<NewsArticle {self.source} - {self.title[:30]}... Classification: {self.classification}>"
