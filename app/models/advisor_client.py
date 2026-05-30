from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class AdvisorClient(Base):
    __tablename__ = "advisor_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advisor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    advisor = relationship("User", lazy="selectin")
    holdings = relationship("PortfolioHolding", back_populates="client", cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self) -> str:
        return f"<AdvisorClient id={self.id} advisor_id={self.advisor_id} name={self.name}>"
