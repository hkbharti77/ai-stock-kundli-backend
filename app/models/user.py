"""
User — User accounts with authentication and subscription plan.
"""

from datetime import datetime
from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(
        String(50), default="Viewer"  # SuperAdmin / OrgAdmin / Analyst / Viewer
    )
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)

    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan: Mapped[str] = mapped_column(
        String(20), default="free"  # free / standard / pro
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # ── Subscription Lifecycle ────────────────────────────────────────────────
    # Status: active / trialing / cancelled / expired / past_due / paused
    subscription_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    subscription_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    subscription_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Trial System ─────────────────────────────────────────────────────────
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── Custom White-Label Advisor Branding ──
    advisor_brand_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    advisor_logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    advisor_brand_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    advisor_brand_color_secondary: Mapped[str | None] = mapped_column(String(7), nullable=True)

    # ── Multi-Step SEBI Compliance & Registration Fields ──
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    otp_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dob: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    
    # ── Investor Profile ──
    risk_appetite: Mapped[str | None] = mapped_column(String(50), nullable=True)
    experience: Mapped[str | None] = mapped_column(String(50), nullable=True)
    goal: Mapped[str | None] = mapped_column(String(100), nullable=True)
    horizon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    
    # ── Legal & Mandate ──
    disclaimer_accepted: Mapped[bool] = mapped_column(Boolean, default=False)

    tenant = relationship("Tenant", lazy="selectin")

    def __repr__(self) -> str:
        return f"<User {self.email} plan={self.plan} status={self.subscription_status}>"
