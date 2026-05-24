from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base

class SignalAccuracy(Base):
    __tablename__ = "signal_accuracy"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    signal_label = Column(String(50), nullable=False)
    kundli_score = Column(Integer, nullable=False)
    price_at_signal = Column(Float, nullable=False)
    price_3m_after = Column(Float, nullable=True)
    accuracy_pct = Column(Float, nullable=True)
    created_at = Column(DateTime, default=func.now())
    evaluated_at = Column(DateTime, nullable=True)
