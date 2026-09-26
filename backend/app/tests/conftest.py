"""
テスト共通設定

【概要】
テスト用の環境変数設定とDBセッションのフィクスチャを提供する。
DB接続先は環境変数`TEST_DATABASE_URL`から取得する（未設定のDBテストはスキップする）。
各テストは1つのトランザクション内で実行し、終了時にロールバックして互いに影響しないようにする。
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import Base

# テスト用のJWT秘密鍵（本番の値ではない）
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-0123456789-abcdefghij")


@pytest.fixture(scope="session")
def db_engine():
    """テスト用DBのエンジン。テーブルを作り直す（テスト用DB専用のため既存データは破棄される）"""
    test_database_url = os.environ.get("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("TEST_DATABASE_URLが未設定のためDBテストをスキップします")
    engine = create_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(db_engine) -> Iterator[Session]:
    """1テスト1トランザクションのDBセッション。テスト終了時にロールバックする"""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield session
    session.close()
    transaction.rollback()
    connection.close()
