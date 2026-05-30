"""
IntradayPrice — 5-minute interval OHLCV price data per company with RSI and VWAP.
"""

from datetime import datetime
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class IntradayPrice(Base):
    __tablename__ = "intraday_prices"
    __table_args__ = (
        UniqueConstraint("company_id", "timestamp", name="uq_company_timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    open: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    high: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    low: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    close: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rsi: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    vwap: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    # Relationship
    company = relationship("Company", back_populates="intraday_prices")

    def __repr__(self) -> str:
        return f"<IntradayPrice company_id={self.company_id} {self.timestamp}>"
