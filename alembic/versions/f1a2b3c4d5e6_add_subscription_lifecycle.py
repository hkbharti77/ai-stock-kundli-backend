"""add subscription lifecycle

Revision ID: f1a2b3c4d5e6
Revises: 4e2d730b925f
Create Date: 2026-06-10 16:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '4e2d730b925f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add Subscription Lifecycle Columns
    op.add_column('users', sa.Column('subscription_status', sa.String(length=50), nullable=True))
    op.add_column('users', sa.Column('subscription_started_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('subscription_ends_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('provider_subscription_id', sa.String(length=255), nullable=True))
    
    # Add Trial System Columns
    op.add_column('users', sa.Column('trial_used', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('users', sa.Column('trial_expires_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'trial_expires_at')
    op.drop_column('users', 'trial_used')
    op.drop_column('users', 'provider_subscription_id')
    op.drop_column('users', 'subscription_ends_at')
    op.drop_column('users', 'subscription_started_at')
    op.drop_column('users', 'subscription_status')
