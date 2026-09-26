"""
Alembicマイグレーション実行環境

【概要】
接続先は環境変数`DATABASE_URL`から取得する。テーブル定義は`app.models`のメタデータを対象とする。
"""

from logging.config import fileConfig

from alembic import context

from app.database import get_engine
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    """DBへ接続してマイグレーションを実行する"""
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
