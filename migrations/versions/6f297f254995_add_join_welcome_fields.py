"""add join welcome fields

Revision ID: 6f297f254995
Revises: d8bfbba2e0d8
Create Date: 2026-06-21 10:01:09.827029

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f297f254995'
down_revision: Union[str, Sequence[str], None] = 'd8bfbba2e0d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Колонки уже созданы через create_all (AUTO_INIT_DB), миграция-заглушка.
    pass


def downgrade() -> None:
    pass