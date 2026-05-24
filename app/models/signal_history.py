from datetime import datetime
from sqlalchemy import (
    DateTime,
    Integer,
    String,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class SignalHistory(Base):
    __tablename__ = "signal_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    old_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_score: Mapped[int] = mapped_column(Integer, nullable=False)
    old_signal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_signal: Mapped[str] = mapped_column(String(50), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    company = relationship("Company")

    def __repr__(self) -> str:
        return f"<SignalHistory company_id={self.company_id} {self.old_signal} -> {self.new_signal}>"
