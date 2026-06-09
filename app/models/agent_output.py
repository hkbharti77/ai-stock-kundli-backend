"""
AgentOutput — Stores structured ratings, key strengths, major concerns, and detailed analytical reasoning of AI agents.
"""

from datetime import datetime
from sqlalchemy import (
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    ForeignKey,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class AgentOutput(Base):
    __tablename__ = "agent_outputs"
    __table_args__ = (
        UniqueConstraint("company_id", "agent_type", name="uq_agent_outputs_company_agent"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    trend: Mapped[str | None] = mapped_column(String(20), nullable=True)  # improving, stable, declining
    strengths: Mapped[list | None] = mapped_column(JSON, nullable=True)  # JSON array of strings
    concerns: Mapped[list | None] = mapped_column(JSON, nullable=True)  # JSON array of strings
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)  # Markdown text
    data_completeness: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    agent_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Extra agent-specific structured data
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    company = relationship("Company", back_populates="agent_outputs")

    def __repr__(self) -> str:
        return f"<AgentOutput {self.agent_type} for Company ID {self.company_id} - Score: {self.score}>"
