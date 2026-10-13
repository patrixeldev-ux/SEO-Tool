"""in-app notifications

Revision ID: 7a3c9e5d1b42
Revises: 5b7d1e9c2a10
Create Date: 2026-10-14 08:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '7a3c9e5d1b42'
down_revision: str | None = '5b7d1e9c2a10'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('site_id', sa.Uuid(), nullable=True),
        sa.Column('batch_id', sa.Uuid(), nullable=True),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['deploy_batches.id'], name=op.f('fk_notifications_batch_id_deploy_batches'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_notifications_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], name=op.f('fk_notifications_site_id_sites'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    )
    op.create_index('ix_notifications_org_id_created_at', 'notifications', ['org_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_notifications_org_id_created_at', table_name='notifications')
    op.drop_table('notifications')
