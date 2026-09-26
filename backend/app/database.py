"""
DB接続層

【概要】
PostgreSQLへのエンジン・セッションを生成する。接続先は環境変数`DATABASE_URL`から取得する
（ソースコードに接続情報を記載しない）。

設計書：設計書/アーキテクチャ方針（構成・トランザクション）
"""

import os
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """
    エンジン取得

    【処理概要】
    - 環境変数`DATABASE_URL`からDBエンジンを1つだけ生成して使い回す。

    【戻り値】
    - engine (Engine) : SQLAlchemyのエンジン

    【例外処理】
    - RuntimeError : `DATABASE_URL`が未設定の場合
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URLが未設定です")
    # pool_pre_ping：切断済みの接続を使い回さないよう、利用前に接続の生存を確認する
    return create_engine(database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """
    セッションファクトリー取得

    【処理概要】
    - エンジンに紐づくセッションファクトリーを1つだけ生成して使い回す。
    - `expire_on_commit=False`：commit後もレスポンス組み立てのため属性を参照できるようにする。

    【戻り値】
    - session_factory (sessionmaker[Session]) : セッションファクトリー
    """
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """
    データベースセッション取得

    【処理概要】
    - リクエストごとにDBセッションを生成して提供し、リクエスト終了時に必ず閉じる（未commitの変更は破棄される）。

    【戻り値】
    - db (Iterator[Session]) : DBセッション（FastAPIの依存性注入で利用する）
    """
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
