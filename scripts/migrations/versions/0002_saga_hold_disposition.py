"""B55 — add sagas.hold_disposition column

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-19

Adds a nullable TEXT column `sagas.hold_disposition` with a CHECK constraint
limiting values to ('cancel_safe', 'operator_decides') OR NULL. Saga write
sites populate it at MANUAL_REVIEW transitions so the orphan-hold reconciler
can tell "operator wants this cancelled" from "operator must decide" — see
docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §4.1 B55 for the full rationale.

DDL lives in ../sql/0002_saga_hold_disposition.{up,down}.sql.
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def upgrade() -> None:
    op.execute((_SQL_DIR / "0002_saga_hold_disposition.up.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute((_SQL_DIR / "0002_saga_hold_disposition.down.sql").read_text(encoding="utf-8"))
