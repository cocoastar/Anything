"""add daka_user and daka_record tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-14

"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'daka_user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.String(length=20), nullable=False),
        sa.Column('password_encrypted', sa.String(length=512), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=True, server_default='1'),
        sa.Column('time_window_start', sa.String(length=8), nullable=True, server_default='13:00'),
        sa.Column('time_window_end', sa.String(length=8), nullable=True, server_default='14:00'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('student_id', name='uq_daka_user_student_id')
    )
    op.create_index('ix_daka_user_student_id', 'daka_user', ['student_id'], unique=False)

    op.create_table(
        'daka_record',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.String(length=20), nullable=False),
        sa.Column('record_date', sa.Date(), nullable=False),
        sa.Column('sign_in_at', sa.DateTime(), nullable=True),
        sa.Column('sign_out_at', sa.DateTime(), nullable=True),
        sa.Column('sign_in_lat', sa.String(length=24), nullable=True),
        sa.Column('sign_in_lon', sa.String(length=24), nullable=True),
        sa.Column('sign_out_lat', sa.String(length=24), nullable=True),
        sa.Column('sign_out_lon', sa.String(length=24), nullable=True),
        sa.Column('sign_in_msg', sa.String(length=128), nullable=True),
        sa.Column('sign_out_msg', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('student_id', 'record_date', name='uq_daka_record_student_date')
    )
    op.create_index('ix_daka_record_student_id', 'daka_record', ['student_id'], unique=False)
    op.create_index('ix_daka_record_record_date', 'daka_record', ['record_date'], unique=False)


def downgrade():
    op.drop_index('ix_daka_record_record_date', 'daka_record')
    op.drop_index('ix_daka_record_student_id', 'daka_record')
    op.drop_table('daka_record')
    op.drop_index('ix_daka_user_student_id', 'daka_user')
    op.drop_table('daka_user')
