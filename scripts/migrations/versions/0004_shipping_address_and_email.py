"""Workstream A+D — add checkouts.shipping_address + sagas.emails_sent

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-11

Adds two JSONB columns for the checkout completion build:
  * checkouts.shipping_address  — full validated drop-ship address (nullable),
    collected at /confirm, read by the saga and passed into order_from_lego.
  * sagas.emails_sent           — idempotency ledger for the customer email
    layer (NOT NULL DEFAULT '[]'), so saga retries never double-send.

See docs/CHECKOUT_COMPLETION_PLAN.md §2 (Workstream A) and §5 (Workstream D).
DDL lives in ../sql/0004_shipping_address_and_email.{up,down}.sql.
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def upgrade() -> None:
    op.execute((_SQL_DIR / "0004_shipping_address_and_email.up.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute((_SQL_DIR / "0004_shipping_address_and_email.down.sql").read_text(encoding="utf-8"))
