from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    client_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("advisor_clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shares: Mapped[float] = mapped_column(Numeric(precision=12, scale=4), nullable=False)
    average_price: Mapped[float] = mapped_column(Numeric(precision=12, scale=2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    user = relationship("User", lazy="selectin")
    client = relationship("AdvisorClient", back_populates="holdings", lazy="selectin")
    company = relationship("Company", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_user_company_holding"),
        UniqueConstraint("client_id", "company_id", name="uq_client_company_holding"),
    )

    def __repr__(self) -> str:
        return f"<PortfolioHolding user_id={self.user_id} company_id={self.company_id} shares={self.shares}>"
