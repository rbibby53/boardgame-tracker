"""live game nights

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27 21:30:00

live_nights (+ members, game pile, the game in progress), plays.live_night_id and polls.live_night_id.
Only new tables and nullable ADD COLUMNs (no table rebuilds on SQLite; see 0005).
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('live_nights',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('location', sa.String(length=200), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('host_id', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('ended_reason', sa.String(length=20), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(), nullable=False),
        sa.Column('next_game_id', sa.Integer(), nullable=True),
        sa.Column('current_game_id', sa.Integer(), nullable=True),
        sa.Column('current_keeper_id', sa.Integer(), nullable=True),
        sa.Column('current_state', sa.Text(), nullable=True),
        sa.Column('current_version', sa.Integer(), nullable=False),
        sa.Column('current_started_at', sa.DateTime(), nullable=True),
        sa.Column('current_updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['host_id'], ['players.id'], name=op.f('fk_live_nights_host_id_players'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['next_game_id'], ['games.id'], name=op.f('fk_live_nights_next_game_id_games'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['current_game_id'], ['games.id'], name=op.f('fk_live_nights_current_game_id_games'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['current_keeper_id'], ['players.id'], name=op.f('fk_live_nights_current_keeper_id_players'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_live_nights')))
    op.create_table('live_night_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('night_id', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('joined_at', sa.DateTime(), nullable=False),
        sa.Column('left_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['night_id'], ['live_nights.id'], name=op.f('fk_live_night_members_night_id_live_nights'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], name=op.f('fk_live_night_members_player_id_players'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_live_night_members')),
        sa.UniqueConstraint('night_id', 'player_id', name=op.f('uq_live_night_members_night_id')))
    op.create_index(op.f('ix_live_night_members_night_id'), 'live_night_members', ['night_id'], unique=False)
    op.create_index(op.f('ix_live_night_members_player_id'), 'live_night_members', ['player_id'], unique=False)
    op.create_table('live_night_games',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('night_id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('added_by_id', sa.Integer(), nullable=True),
        sa.Column('added_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['night_id'], ['live_nights.id'], name=op.f('fk_live_night_games_night_id_live_nights'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_live_night_games_game_id_games'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['added_by_id'], ['players.id'], name=op.f('fk_live_night_games_added_by_id_players'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_live_night_games')),
        sa.UniqueConstraint('night_id', 'game_id', name=op.f('uq_live_night_games_night_id')))
    op.create_index(op.f('ix_live_night_games_night_id'), 'live_night_games', ['night_id'], unique=False)
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE plays ADD COLUMN live_night_id INTEGER "
                   "CONSTRAINT fk_plays_live_night_id_live_nights REFERENCES live_nights (id) ON DELETE SET NULL")
        op.execute("ALTER TABLE polls ADD COLUMN live_night_id INTEGER "
                   "CONSTRAINT fk_polls_live_night_id_live_nights REFERENCES live_nights (id) ON DELETE CASCADE")
    else:
        op.add_column('plays', sa.Column('live_night_id', sa.Integer(), nullable=True))
        op.create_foreign_key(op.f('fk_plays_live_night_id_live_nights'), 'plays', 'live_nights', ['live_night_id'], ['id'], ondelete='SET NULL')
        op.add_column('polls', sa.Column('live_night_id', sa.Integer(), nullable=True))
        op.create_foreign_key(op.f('fk_polls_live_night_id_live_nights'), 'polls', 'live_nights', ['live_night_id'], ['id'], ondelete='CASCADE')
    op.create_index(op.f('ix_plays_live_night_id'), 'plays', ['live_night_id'], unique=False)
    op.create_index(op.f('ix_polls_live_night_id'), 'polls', ['live_night_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_polls_live_night_id'), table_name='polls')
    op.drop_index(op.f('ix_plays_live_night_id'), table_name='plays')
    if op.get_bind().dialect.name != "sqlite":  # SQLite can't drop a column with a REFERENCES clause; harmless
        op.drop_constraint(op.f('fk_polls_live_night_id_live_nights'), 'polls', type_='foreignkey')
        op.drop_column('polls', 'live_night_id')
        op.drop_constraint(op.f('fk_plays_live_night_id_live_nights'), 'plays', type_='foreignkey')
        op.drop_column('plays', 'live_night_id')
    op.drop_index(op.f('ix_live_night_games_night_id'), table_name='live_night_games')
    op.drop_table('live_night_games')
    op.drop_index(op.f('ix_live_night_members_player_id'), table_name='live_night_members')
    op.drop_index(op.f('ix_live_night_members_night_id'), table_name='live_night_members')
    op.drop_table('live_night_members')
    op.drop_table('live_nights')
