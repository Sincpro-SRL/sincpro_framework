"""${message}

Never import domain or ORM models here: they change, and this step must still run as it was
written. When it touches data, describe the tables it needs with `sa.table()`.
"""

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision = "${up_revision}"
down_revision = ${'"%s"' % down_revision if down_revision else "None"}
branch_labels = None
depends_on = None


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
