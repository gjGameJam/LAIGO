"""initial schema — jobs, checkouts, sagas, payment_holds, audit_events, schema_meta

Revision ID: 0001
Revises:
Create Date: 2026-05-16

DDL lives in ../sql/0001_initial_schema.{up,down}.sql so the schema is
readable as plain SQL (operator dashboards, psql replay, code review). This
Python file is the thin alembic wrapper that op.execute()s those files.

When adding a new migration:
  1. Write 000N_<slug>.up.sql + 000N_<slug>.down.sql in ../sql/
  2. Write 000N_<slug>.py here with revision='000N', down_revision='<prev>'
  3. Bump _EXPECTED_SCHEMA_VERSION in scripts/db.py to '000N'

See docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.5.B.
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def upgrade() -> None:
    op.execute((_SQL_DIR / "0001_initial_schema.up.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute((_SQL_DIR / "0001_initial_schema.down.sql").read_text(encoding="utf-8"))
