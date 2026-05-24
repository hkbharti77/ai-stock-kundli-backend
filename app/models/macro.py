"""
AI Stock Kundli — Macroeconomic Data Model
Stores system-wide domestic and international macroeconomic variables.
"""

from datetime import datetime
from sqlalchemy import Integer, Numeric, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class MacroData(Base):
    __tablename__ = "macro_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    indicator: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # e.g., "repo_rate", "cpi_inflation", "fii_flows_monthly", "inr_usd"
    value: Mapped[float] = mapped_column(Numeric, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g., "2026-05"
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<MacroData indicator={self.indicator} value={self.value} period={self.period}>"
