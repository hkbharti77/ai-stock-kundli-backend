"""
SocialSignal Model — Sprint 12
Stores social media signals crawled from major financial handles on Twitter/X.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship

from app.core.database import Base


class SocialSignal(Base):
    """Tracks social media commentary and sentiment weight."""
    __tablename__ = "social_signals"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    handle = Column(String, index=True)  # e.g., "@CNBCTV18News", "@moneycontrolcom"
    content = Column(String, nullable=False)
    sentiment = Column(String, index=True)  # "positive" | "negative" | "neutral"
    sentiment_score = Column(Float, default=0.0)  # -100.0 to +100.0
    followers_count = Column(Integer, default=0)
    posted_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    company = relationship("Company", back_populates="social_signals")
