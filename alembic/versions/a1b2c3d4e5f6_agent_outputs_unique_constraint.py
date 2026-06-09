"""Add unique constraint on agent_outputs(company_id, agent_type) and deduplicate

Revision ID: a1b2c3d4e5f6
Revises: 035f911f85ce
Create Date: 2026-06-09 23:45:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '035f911f85ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Step 1: Delete duplicate rows, keeping only the most-recently updated one
    # for each (company_id, agent_type) pair.
    op.execute("""
        DELETE FROM agent_outputs
        WHERE id NOT IN (
            SELECT DISTINCT ON (company_id, agent_type) id
            FROM agent_outputs
            ORDER BY company_id, agent_type, updated_at DESC NULLS LAST
        )
    """)

    # ── Step 2: Add unique constraint so duplicates can never be inserted again
    op.create_unique_constraint(
        'uq_agent_outputs_company_agent',
        'agent_outputs',
        ['company_id', 'agent_type']
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_agent_outputs_company_agent',
        'agent_outputs',
        type_='unique'
    )
