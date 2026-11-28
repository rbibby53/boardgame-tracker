"""round-winner scoring, round names, expansions, favorites

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-27 20:45:00

- plays.scoring: how a play was scored when it differs from its game ("winloss" = a points game such as Mexican
  Train played as "who won each round, most rounds wins"); NULL = the game's own scoring
- games.round_names: preset round labels, one per line (Mexican Train: "Double 12" ... "Double blank")
- game_expansions (+ owned flag) and play_expansions: which expansions a play used
- favorites: each player's favorite games
Only nullable ADD COLUMNs and new tables, so SQLite never rebuilds a table (see 0005 for why that matters).
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('games', sa.Column('round_names', sa.Text(), nullable=True))
    op.add_column('plays', sa.Column('scoring', sa.String(length=10), nullable=True))
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
    op.drop_column('plays', 'scoring')
    op.drop_column('games', 'round_names')
