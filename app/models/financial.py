"""
Financial — Annual and quarterly financial data per company.
"""

from datetime import date, datetime
from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Financial(Base):
    __tablename__ = "financials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id"), nullable=False, index=True
    )
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 'annual' / 'quarterly'
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Profitability
    revenue: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    gross_profit: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ebitda: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    pat: Mapped[float | None] = mapped_column(Numeric, nullable=True)  # Profit After Tax
    eps: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    # Returns
    roe: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    roce: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    # Leverage & Liquidity
    debt_equity: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    current_ratio: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    # Cash Flow
    operating_cash_flow: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    free_cash_flow: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationship
    company = relationship("Company", back_populates="financials")

    def __repr__(self) -> str:
        return f"<Financial company_id={self.company_id} {self.period_type} {self.period_end}>"
