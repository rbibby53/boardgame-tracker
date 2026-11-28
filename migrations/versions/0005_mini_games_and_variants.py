"""mini-games and variants: a game can be part of another (games.parent_id, games.is_box)

A parent with is_box set is a box of games that isn't played itself (a Jackbox Party Pack): its children are
mini-games and follow its shelf/archive status. Any other parent is a playable base game (Uno): its children
are variants (Uno Flip) with their own shelf status.
Also games.round_names (preset round labels), plays.scoring (a points game played as round wins), game expansions
with an owned flag + which ones each play used, and per-player favorite games.

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
    # plain nullable columns: a simple ADD COLUMN everywhere (no table rebuild on SQLite)
    op.add_column('games', sa.Column('round_names', sa.Text(), nullable=True))
    op.add_column('plays', sa.Column('scoring', sa.String(length=10), nullable=True))
    op.create_index(op.f('ix_games_parent_id'), 'games', ['parent_id'], unique=False)

    op.create_table('game_expansions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('owned', sa.Boolean(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_game_expansions_game_id_games'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_game_expansions')),
        sa.UniqueConstraint('game_id', 'name', name=op.f('uq_game_expansions_game_id')))
    op.create_index(op.f('ix_game_expansions_game_id'), 'game_expansions', ['game_id'], unique=False)
    op.create_table('play_expansions',
        sa.Column('play_id', sa.Integer(), nullable=False),
        sa.Column('expansion_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['expansion_id'], ['game_expansions.id'], name=op.f('fk_play_expansions_expansion_id_game_expansions'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['play_id'], ['plays.id'], name=op.f('fk_play_expansions_play_id_plays'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('play_id', 'expansion_id', name=op.f('pk_play_expansions')))
    op.create_table('favorites',
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_favorites_game_id_games'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], name=op.f('fk_favorites_player_id_players'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('player_id', 'game_id', name=op.f('pk_favorites')))
    op.create_index(op.f('ix_favorites_game_id'), 'favorites', ['game_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_favorites_game_id'), table_name='favorites')
    op.drop_table('favorites')
    op.drop_table('play_expansions')
    op.drop_index(op.f('ix_game_expansions_game_id'), table_name='game_expansions')
    op.drop_table('game_expansions')
    op.drop_index(op.f('ix_games_parent_id'), table_name='games')
    if op.get_bind().dialect.name == "sqlite":
        # SQLite 3.35+ can drop a plain column, but not one with a REFERENCES clause; leave them (harmless).
        return
    op.drop_column('plays', 'scoring')
    op.drop_column('games', 'round_names')
    op.drop_column('games', 'is_box')
    op.drop_constraint(op.f('fk_games_parent_id_games'), 'games', type_='foreignkey')
    op.drop_column('games', 'parent_id')
