"""needs_manual action status

Revision ID: 5b7d1e9c2a10
Revises: 4f6e304a8a20
Create Date: 2026-10-13 02:20:59.204662
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '5b7d1e9c2a10'
down_revision: str | None = '4f6e304a8a20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUSES = "'proposed', 'needs_ai', 'no_suggestion', 'approved', 'deployed', 'rejected', 'rolled_back', 'manual_done', 'stale', 'obsolete'"
_OPEN = "'proposed', 'needs_ai', 'no_suggestion', 'approved', 'deployed'"


def _reindex(open_list: str) -> None:
    op.drop_index('uq_actions_open_key', table_name='actions')
    op.create_index(
        'uq_actions_open_key', 'actions',
        ['site_id', 'type', sa.text("coalesce(target_url, '')"), 'field'],
        unique=True, postgresql_where=sa.text(f"status IN ({open_list})"),
    )


def upgrade() -> None:
    # a new action status; it occupies the "one open action per slot" key like the other open statuses
    op.drop_constraint('status', 'actions', type_='check')
    op.create_check_constraint('status', 'actions', "status IN (" + _STATUSES.replace("'stale'", "'needs_manual', 'stale'") + ")")
    _reindex(_OPEN + ", 'needs_manual'")


def downgrade() -> None:
    op.execute("UPDATE actions SET status = 'approved' WHERE status = 'needs_manual'")
    _reindex(_OPEN)
    op.drop_constraint('status', 'actions', type_='check')
    op.create_check_constraint('status', 'actions', f"status IN ({_STATUSES})")
