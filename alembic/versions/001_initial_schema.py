"""Initial schema — companies, financials, price_history, users

Revision ID: 001_initial
Revises: None
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Companies ────────────────────────────────────────
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("isin", sa.String(12), unique=True, nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("sub_sector", sa.String(100), nullable=True),
        sa.Column("exchange", sa.String(10), nullable=True),
        sa.Column("market_cap", sa.Numeric(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_companies_ticker", "companies", ["ticker"])

    # ── Financials ───────────────────────────────────────
    op.create_table(
        "financials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("period_type", sa.String(10), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("revenue", sa.Numeric(), nullable=True),
        sa.Column("gross_profit", sa.Numeric(), nullable=True),
        sa.Column("ebitda", sa.Numeric(), nullable=True),
        sa.Column("pat", sa.Numeric(), nullable=True),
        sa.Column("eps", sa.Numeric(), nullable=True),
        sa.Column("roe", sa.Numeric(), nullable=True),
        sa.Column("roce", sa.Numeric(), nullable=True),
        sa.Column("debt_equity", sa.Numeric(), nullable=True),
        sa.Column("current_ratio", sa.Numeric(), nullable=True),
        sa.Column("operating_cash_flow", sa.Numeric(), nullable=True),
        sa.Column("free_cash_flow", sa.Numeric(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_financials_company_id", "financials", ["company_id"])

    # ── Price History ────────────────────────────────────
    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=True),
        sa.Column("high", sa.Numeric(), nullable=True),
        sa.Column("low", sa.Numeric(), nullable=True),
        sa.Column("close", sa.Numeric(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("company_id", "date", name="uq_company_date"),
    )
    op.create_index("ix_price_history_company_id", "price_history", ["company_id"])

    # ── Users ────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("plan", sa.String(20), server_default="free"),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])


def downgrade() -> None:
    op.drop_table("price_history")
    op.drop_table("financials")
    op.drop_table("users")
    op.drop_table("companies")
