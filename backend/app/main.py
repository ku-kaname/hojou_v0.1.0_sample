"""
エンドポイント層（main）

【概要】
FastAPIアプリケーションの組み立てと、HTTPの入口となるエンドポイントを定義する。
すべてのAPIは`/api`配下に配置する。機能群ごとのエンドポイントは、実装の進行に伴い本ファイルへ追加する。
共通部分として、アプリの起動時検証・想定外エラーの共通応答・ヘルスチェックを持つ。

設計書：設計書/エンドポイント、設計書/サーバー処理（main）/共通/ヘルスチェック
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import validate_jwt_settings
from app.database import get_db
from app.schemas import HealthResponse

logger = logging.getLogger("app")

api_router = APIRouter(prefix="/api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """
    アプリ起動・終了時の処理

    【処理概要】
    - 起動時にJWT秘密鍵の設定不備を検出し、不備があれば起動を中止する（RuntimeError）。
    """
    validate_jwt_settings()
    yield


def _is_api_docs_enabled() -> bool:
    """APIドキュメント（Swagger UI等）の公開要否。本番では非公開とし、環境変数ENABLE_API_DOCSが"1"のときのみ公開する"""
    return os.environ.get("ENABLE_API_DOCS", "") == "1"


_docs_enabled = _is_api_docs_enabled()
app = FastAPI(
    title="社内備品貸出管理システム",
    lifespan=lifespan,
    docs_url="/api/docs" if _docs_enabled else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if _docs_enabled else None,
)


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException) -> Response:
    """
    HTTPエラーの共通応答（認証・認可エラーのエンドポイント記録）

    設計書：設計書/アーキテクチャ方針

    【処理概要】
    - 401（認証エラー）・403（権限エラー）の場合、エンドポイントと結果をサーバーログに記録する
      （ユーザー内部IDは認証・認可側のログに記録される）。
    - 応答の内容はFastAPI標準のままとする。
    """
    if exc.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
        logger.warning("認証・認可エラー応答: %s %s 結果=%s", request.method, request.url.path, exc.status_code)
    response = await http_exception_handler(request, exc)
    return response


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """
    想定外エラーの共通応答

    設計書：設計書/アーキテクチャ方針

    【処理概要】
    - 想定外の例外は、内部情報（SQL・スタックトレース等）を含めず500「サーバーエラーが発生しました」を返す。
    - 詳細はサーバーログのみに記録する（リクエストの入力値は記録しない）。
    """
    error_type = type(exc).__name__
    logger.error("想定外のエラー: %s %s (%s)", request.method, request.url.path, error_type, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "サーバーエラーが発生しました"},
    )


@api_router.get("/health", response_model=HealthResponse)
def health_check(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
    """
    ヘルスチェック

    設計書：設計書/サーバー処理（main）/共通/ヘルスチェック

    【処理概要】
    - Docker Composeの死活監視のため、バックエンドがDBへ接続できるかを返却する。
    - 認証は不要。内部情報（バージョン・接続先等）は返却しない。

    【パラメータ】
    - db (Session) : DBセッション

    【戻り値】
    - health_response (HealthResponse) : 状態（固定値ok）

    【例外処理】
    - HTTPException(503) : DBへ接続できない場合（"サービスを利用できません"。原因はサーバーログのみに記録する）

    【処理フロー】
    1. DB接続の確認（SELECT 1）
    2. 戻り値を設定
    """
    # 1. DB接続の確認
    try:
        select_one = text("SELECT 1")
        db.execute(select_one)
    except SQLAlchemyError as error:
        error_type = type(error).__name__
        logger.error("ヘルスチェックでDB接続に失敗しました (%s)", error_type)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="サービスを利用できません",
        ) from None

    # 2. 戻り値を設定
    health_response = HealthResponse(status="ok")
    return health_response


app.include_router(api_router)
