from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class AlertHistory(Base):
    """
    Logs active deliveries of notifications.
    Supports 1-hour deduplication analysis and feeds the visual settings log feed.
    """
    __tablename__ = "alert_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    alert_rule_id = Column(Integer, ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    severity = Column(String, default="info")  # info, medium, high, critical
    channel = Column(String, nullable=False)  # push, email
    delivered_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    alert_rule = relationship("AlertRule")
    company = relationship("Company", backref="alert_history")
