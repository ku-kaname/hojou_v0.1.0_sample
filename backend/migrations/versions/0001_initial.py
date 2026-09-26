"""初期テーブルの作成

【概要】
app_user・equipment・loan_request・notificationの各テーブルと、制約・インデックスを作成する。
二重貸出防止の排他制約にbtree_gist拡張が必要なため、最初に有効化する。

設計書：設計書/テーブル定義（models）

Revision ID: 0001
Revises:
"""

from alembic import op

from app.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """拡張機能を有効化し、モデル定義どおりに全テーブルを作成する"""
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    """全テーブルを削除する（拡張機能は他の用途の可能性があるため残す）"""
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
