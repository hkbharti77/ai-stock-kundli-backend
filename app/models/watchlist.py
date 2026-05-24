"""
Watchlist — Model for mapping users to saved companies for quick tracking.
"""

from datetime import datetime
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", lazy="selectin")
    company = relationship("Company", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_user_company_watchlist"),
    )

    def __repr__(self) -> str:
        return f"<Watchlist user_id={self.user_id} company_id={self.company_id}>"
