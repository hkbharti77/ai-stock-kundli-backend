from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base

class AgentRunLog(Base):
    __tablename__ = "agent_run_logs"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    agent_type = Column(String(50), nullable=False)
    latency_ms = Column(Float, nullable=False)
    error_occurred = Column(Boolean, default=False)
    error_message = Column(String(500), nullable=True)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    cost_inr = Column(Float, default=0.0)
    created_at = Column(DateTime, default=func.now())
