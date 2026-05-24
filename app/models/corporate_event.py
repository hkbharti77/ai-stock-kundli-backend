"""
CorporateEvent Model — Sprint 12
Captures major events like stock splits, dividends, bonuses, mergers, and management changes.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Date, ForeignKey, DateTime
from sqlalchemy.orm import relationship

from app.core.database import Base


class CorporateEvent(Base):
    """Tracks major corporate actions for a company."""
    __tablename__ = "corporate_events"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String, index=True)  # "dividend" | "split" | "bonus" | "merger" | "management_change"
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    event_date = Column(Date, nullable=False)
    announced_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    company = relationship("Company", back_populates="corporate_events")
