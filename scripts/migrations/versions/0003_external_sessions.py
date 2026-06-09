"""external_sessions — Playwright storage_state per external provider

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-09

Adds the `external_sessions` table for caching Playwright `storage_state`
JSON per external account (first consumer: provider='lego'). Enables
LEGO.com ordering to skip the email-only 2FA challenge by reusing a
manually-seeded session — see scripts/checkout/clients/lego_session_store.py
and scripts/seed_lego_session.py for the full flow.

DDL lives in ../sql/0003_external_sessions.{up,down}.sql.
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def upgrade() -> None:
    op.execute((_SQL_DIR / "0003_external_sessions.up.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute((_SQL_DIR / "0003_external_sessions.down.sql").read_text(encoding="utf-8"))
