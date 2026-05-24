from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class AlertRule(Base):
    """
    Stores user-configured trigger metrics for stocks.
    Supports price shifts %, volume multiples, news alerts, and sentiment spikes.
    """
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True)
    trigger_type = Column(String, nullable=False)  # price_movement, volume_spike, news_event, earnings_surprise, sentiment_shift, technical_breakout, risk_flag, signal_change
    threshold_value = Column(Float, nullable=True)  # threshold parameter
    delivery_channel = Column(String, default="both")  # push, email, both
    is_active = Column(Boolean, default=True)
    quiet_hours_enabled = Column(Boolean, default=True)
    muted_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    company = relationship("Company", backref="alert_rules")
