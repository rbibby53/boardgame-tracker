"""mini-games and variants: a game can be part of another (games.parent_id, games.is_box)

A parent with is_box set is a box of games that isn't played itself (a Jackbox Party Pack): its children are
mini-games and follow its shelf/archive status. Any other parent is a playable base game (Uno): its children
are variants (Uno Flip) with their own shelf status.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-27 19:40:00

On SQLite this deliberately avoids batch mode. Batch mode rebuilds the games table (copy, drop, rename), and
with PRAGMA foreign_keys=ON the drop cascades to game_categories and poll_options. A plain ADD COLUMN with a
NULL default is allowed to carry a REFERENCES clause and leaves every row where it is.
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE games ADD COLUMN parent_id INTEGER "
                   "CONSTRAINT fk_games_parent_id_games REFERENCES games (id) ON DELETE SET NULL")
        op.execute("ALTER TABLE games ADD COLUMN is_box BOOLEAN NOT NULL DEFAULT 0")
    else:
        op.add_column('games', sa.Column('is_box', sa.Boolean(), nullable=False, server_default=sa.false()))
        op.add_column('games', sa.Column('parent_id', sa.Integer(), nullable=True))
        op.create_foreign_key(op.f('fk_games_parent_id_games'), 'games', 'games', ['parent_id'], ['id'],
                              ondelete='SET NULL')
    op.create_index(op.f('ix_games_parent_id'), 'games', ['parent_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_games_parent_id'), table_name='games')
    if op.get_bind().dialect.name == "sqlite":
        # SQLite 3.35+ can drop a plain column, but not one with a REFERENCES clause; leave them (harmless).
        return
    op.drop_column('games', 'is_box')
    op.drop_constraint(op.f('fk_games_parent_id_games'), 'games', type_='foreignkey')
    op.drop_column('games', 'parent_id')
