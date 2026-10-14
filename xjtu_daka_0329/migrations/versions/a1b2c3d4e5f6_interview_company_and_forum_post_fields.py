"""interview company and forum_post company_id interview_type

Revision ID: a1b2c3d4e5f6
Revises: 3ffc7f3980b0
Create Date: 2026-03-14

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '3ffc7f3980b0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'interview_company',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_interview_company_name')
    )
    with op.batch_alter_table('forum_post', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('interview_type', sa.String(32), nullable=True))


def downgrade():
    with op.batch_alter_table('forum_post', schema=None) as batch_op:
        batch_op.drop_column('interview_type')
        batch_op.drop_column('company_id')
    op.drop_table('interview_company')
