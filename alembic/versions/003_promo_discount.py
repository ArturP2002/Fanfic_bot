"""Скидка в процентах для промокодов.

Revision ID: 003_promo_discount
Revises: 002_subscription_promo
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_promo_discount"
down_revision: Union[str, None] = "002_subscription_promo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "promo_codes",
        sa.Column(
            "discount_percent",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("promo_codes", "discount_percent")
