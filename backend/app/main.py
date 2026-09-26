"""
エンドポイント層（main）

【概要】
FastAPIアプリケーションの組み立てと、HTTPの入口となるエンドポイントを定義する。
すべてのAPIは`/api`配下に配置する。機能群ごとのエンドポイントは、実装の進行に伴い本ファイルへ追加する。
共通部分として、アプリの起動時検証・想定外エラーの共通応答・ヘルスチェックを持つ。
認証・ユーザー管理の機能群として、ログイン・ログアウト・パスワード変更・ユーザー管理のエンドポイントと、
起動時の初期管理者作成を持つ。
備品管理の機能群として、備品の検索・取得・登録・編集・分類一覧・予約状況・CSV一括登録のエンドポイントを持つ。

設計書：設計書/エンドポイント、設計書/サーバー処理（main）/共通/ヘルスチェック、
設計書/サーバー処理（main）/認証・ユーザー管理/、
設計書/サーバー処理（main）/備品管理/
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Path, Query, Request, UploadFile, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import services
from app.auth import get_current_active_user, get_current_admin_user, get_current_user, validate_jwt_settings
from app.database import get_db, get_session_factory
from app.schemas import (
    AuthenticatedUser,
    CategoryListResponse,
    CsvImportErrorResponse,
    CsvImportResponse,
    EquipmentCreateRequest,
    EquipmentListQuery,
    EquipmentResponse,
    EquipmentUpdateRequest,
    HealthResponse,
    LoginRequest,
    MeResponse,
    Page,
    PasswordChangeRequest,
    PasswordResetRequest,
    ReservationListResponse,
    TokenResponse,
    UserCreateRequest,
    UserListQuery,
    UserResponse,
    UserUpdateRequest,
)

logger = logging.getLogger("app")

api_router = APIRouter(prefix="/api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """
    アプリ起動・終了時の処理

    【処理概要】
    - 起動時にJWT秘密鍵の設定不備を検出し、不備があれば起動を中止する（RuntimeError）。
    - 有効な管理者が1人もいない場合のみ、環境変数の情報で初期管理者を作成する（設計書「初期管理者作成」）。
      環境変数の不備があれば起動を中止する（RuntimeError）。
    """
    validate_jwt_settings()
    session_factory = get_session_factory()
    with session_factory() as db:
        services.ensure_initial_admin(db)
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


@app.exception_handler(services.CsvImportError)
async def handle_csv_import_error(_request: Request, exc: services.CsvImportError) -> JSONResponse:
    """
    CSV一括登録の内容エラーの応答

    設計書：設計書/サーバー処理（main）/備品管理/備品CSV一括登録

    【処理概要】
    - 行ごとの検証エラー（行番号・列名・エラー内容）を、ステータスコード400のCSVエラーレスポンスへ変換する。
    """
    error_response = CsvImportErrorResponse(detail=str(exc), errors=exc.errors)
    content = error_response.model_dump()
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=content)


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


@api_router.post("/auth/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    """
    ログイン

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ログイン

    【処理概要】
    - ユーザーID・パスワードで認証し、アクセストークンを発行する。認証は不要。
    - 失敗の原因（不在・無効・ロック中・不一致）によらず同一の401を返す。

    【パラメータ】
    - request (LoginRequest) : ログインリクエスト
    - db (Session) : DBセッション

    【戻り値】
    - token_response (TokenResponse) : アクセストークン・"bearer"・初回パスワード変更要否

    【例外処理】
    - HTTPException(401) : 認証失敗（"ユーザーIDまたはパスワードが正しくありません"）

    【処理フロー】
    1. ログイン認証（services.login_user）
    2. 認証失敗の応答
    3. 戻り値を設定
    """
    # 1. ログイン認証
    token_response = services.login_user(db, request)

    # 2. 認証失敗の応答
    if token_response is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザーIDまたはパスワードが正しくありません",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. 戻り値を設定
    return token_response


@api_router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> Response:
    """
    ログアウト

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ログアウト

    【処理概要】
    - ログイン状態を終了し、発行済みトークンをすべて失効させる（初期パスワード未変更でも可）。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー

    【戻り値】
    - なし（204。レスポンスボディなし）

    【例外処理】
    - HTTPException(401) : 認証エラー（認証・認可層）

    【処理フロー】
    1. ログアウト処理（services.logout_user）
    2. 戻り値を設定
    """
    # 1. ログアウト処理
    services.logout_user(db, authenticated_user)

    # 2. 戻り値を設定
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.get("/users/me", response_model=MeResponse)
def get_me(
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> MeResponse:
    """
    自分の情報取得

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/自分の情報取得

    【処理概要】
    - ログイン中のユーザー自身の情報を返却する（画面の表示制御用。初期パスワード未変更でも可）。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー

    【戻り値】
    - me_response (MeResponse) : 自分のユーザー情報

    【例外処理】
    - HTTPException(401) : 認証エラー・ユーザーが取得できない場合

    【処理フロー】
    1. 自分の情報取得（services.get_my_profile）
    2. 戻り値を設定
    """
    # 1. 自分の情報取得
    me_response = services.get_my_profile(db, authenticated_user)

    # 2. 戻り値を設定
    return me_response


@api_router.put("/users/me/password", response_model=TokenResponse)
def change_password(
    request: PasswordChangeRequest,
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> TokenResponse:
    """
    パスワード変更

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/パスワード変更

    【処理概要】
    - ログイン中のユーザーが自分のパスワードを変更する（初回パスワード変更を含む）。
    - 発行済みトークンを失効させ、新しいトークンを返却する。

    【パラメータ】
    - request (PasswordChangeRequest) : パスワード変更リクエスト
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー

    【戻り値】
    - token_response (TokenResponse) : 新しいトークン

    【例外処理】
    - HTTPException(400) : 現在のパスワードの誤り・ロック中・新旧パスワードが同一
    - HTTPException(401) : 認証エラー

    【処理フロー】
    1. パスワード変更処理（services.change_own_password）
    2. 戻り値を設定
    """
    # 1. パスワード変更処理
    token_response = services.change_own_password(db, authenticated_user, request)

    # 2. 戻り値を設定
    return token_response


@api_router.post("/admin/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user_endpoint(
    request: UserCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_admin_user)],
) -> UserResponse:
    """
    ユーザー登録

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ユーザー登録

    【処理概要】
    - 管理者が新しいユーザーを登録する。初回ログイン時にパスワード変更を強制する。

    【パラメータ】
    - request (UserCreateRequest) : ユーザー登録リクエスト
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）

    【戻り値】
    - user_response (UserResponse) : 登録したユーザー（201）

    【例外処理】
    - HTTPException(409) : ユーザーIDの重複
    - HTTPException(401・403) : 認証・権限エラー

    【処理フロー】
    1. ユーザー登録処理（services.register_user）
    2. 戻り値を設定
    """
    # 1. ユーザー登録処理
    user_response = services.register_user(db, request)

    # 2. 戻り値を設定
    return user_response


@api_router.get("/admin/users", response_model=Page[UserResponse])
def list_users(
    query: Annotated[UserListQuery, Query()],
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_admin_user)],
) -> Page[UserResponse]:
    """
    ユーザー一覧取得

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ユーザー一覧取得

    【処理概要】
    - 管理者が、無効化済みを含むユーザーを検索・一覧表示する。

    【パラメータ】
    - query (UserListQuery) : ユーザー一覧クエリ
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）

    【戻り値】
    - page_response (Page[UserResponse]) : ページ形式のユーザー一覧

    【例外処理】
    - HTTPException(401・403) : 認証・権限エラー

    【処理フロー】
    1. ユーザー一覧取得処理（services.list_users_admin）
    2. 戻り値を設定
    """
    # 1. ユーザー一覧取得処理
    page_response = services.list_users_admin(db, query)

    # 2. 戻り値を設定
    return page_response


@api_router.get("/admin/users/{user_id}", response_model=UserResponse)
def get_user_endpoint(
    user_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_admin_user)],
) -> UserResponse:
    """
    ユーザー取得

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ユーザー取得

    【処理概要】
    - 管理者が、ユーザー1件の詳細を取得する（編集画面の初期表示用）。

    【パラメータ】
    - user_id (int) : ユーザー内部ID（1以上）
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）

    【戻り値】
    - user_response (UserResponse) : ユーザー

    【例外処理】
    - HTTPException(404) : ユーザーが存在しない場合
    - HTTPException(401・403) : 認証・権限エラー

    【処理フロー】
    1. ユーザー取得処理（services.get_user_admin）
    2. 戻り値を設定
    """
    # 1. ユーザー取得処理
    user_response = services.get_user_admin(db, user_id)

    # 2. 戻り値を設定
    return user_response


@api_router.put("/admin/users/{user_id}", response_model=UserResponse)
def update_user_endpoint(
    user_id: Annotated[int, Path(ge=1)],
    request: UserUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_admin_user)],
) -> UserResponse:
    """
    ユーザー編集

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ユーザー編集

    【処理概要】
    - 管理者がユーザーの氏名・所属・ロール・有効フラグを更新する。
    - 有効な管理者が0人にならないことを保証し、無効化時は未貸出の申請を自動取消する。

    【パラメータ】
    - user_id (int) : ユーザー内部ID（1以上）
    - request (UserUpdateRequest) : ユーザー編集リクエスト
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）

    【戻り値】
    - user_response (UserResponse) : 更新後のユーザー

    【例外処理】
    - HTTPException(400) : 最後の有効な管理者の無効化・ロール変更、貸出中の申請があるユーザーの無効化
    - HTTPException(404) : ユーザーが存在しない場合
    - HTTPException(401・403) : 認証・権限エラー

    【処理フロー】
    1. ユーザー編集処理（services.update_user_admin）
    2. 戻り値を設定
    """
    # 1. ユーザー編集処理
    user_response = services.update_user_admin(db, user_id, request)

    # 2. 戻り値を設定
    return user_response


@api_router.post("/admin/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(
    user_id: Annotated[int, Path(ge=1)],
    request: PasswordResetRequest,
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_admin_user)],
) -> Response:
    """
    パスワード初期化

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/パスワード初期化

    【処理概要】
    - 管理者が、パスワードを忘れたユーザーのパスワードを初期パスワードに再設定する。
    - 初回パスワード変更を要求し、発行済みトークンの失効とロック解除を行う。

    【パラメータ】
    - user_id (int) : ユーザー内部ID（1以上）
    - request (PasswordResetRequest) : パスワード初期化リクエスト
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）

    【戻り値】
    - なし（204。レスポンスボディなし）

    【例外処理】
    - HTTPException(404) : ユーザーが存在しない場合
    - HTTPException(401・403) : 認証・権限エラー

    【処理フロー】
    1. パスワード初期化処理（services.reset_password_admin）
    2. 戻り値を設定
    """
    # 1. パスワード初期化処理
    services.reset_password_admin(db, user_id, request)

    # 2. 戻り値を設定
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 備品管理 ----

# 備品CSVの最大サイズ（5MB）
CSV_MAX_BYTES = 5 * 1024 * 1024


@api_router.get("/equipments", response_model=Page[EquipmentResponse])
def list_equipments(
    query: Annotated[EquipmentListQuery, Query()],
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_active_user)],
) -> Page[EquipmentResponse]:
    """
    備品一覧検索

    設計書：設計書/サーバー処理（main）/備品管理/備品一覧検索

    【処理概要】
    - 備品を分類・キーワード・貸出状況で検索し、現在の貸出状況とともに一覧表示する。
    - 無効化済みの備品を含める指定は管理者のみ可能とする。

    【パラメータ】
    - query (EquipmentListQuery) : 備品一覧クエリ
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効ユーザー。初期パスワード変更済み）

    【戻り値】
    - page_response (Page[EquipmentResponse]) : ページ形式の備品一覧

    【例外処理】
    - HTTPException(403) : 無効化済みを含める指定が、管理者以外から行われた場合
    - HTTPException(401) : 認証エラー

    【処理フロー】
    1. 備品一覧検索処理（services.search_equipments）
    2. 戻り値を設定
    """
    # 1. 備品一覧検索処理
    page_response = services.search_equipments(db, authenticated_user, query)

    # 2. 戻り値を設定
    return page_response


# 固定パス`/equipments/categories`は、パスパラメーター付きの`/equipments/{equipment_id}`より先に登録する
@api_router.get("/equipments/categories", response_model=CategoryListResponse)
def list_categories(
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_active_user)],
) -> CategoryListResponse:
    """
    分類一覧取得

    設計書：設計書/サーバー処理（main）/備品管理/分類一覧取得

    【処理概要】
    - 備品検索の絞り込み選択肢として、有効な備品の分類を返す。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効ユーザー。初期パスワード変更済み）

    【戻り値】
    - category_response (CategoryListResponse) : 分類一覧（0件は空配列）

    【例外処理】
    - HTTPException(401) : 認証エラー

    【処理フロー】
    1. 分類一覧取得処理（services.list_equipment_categories）
    2. 戻り値を設定
    """
    # 1. 分類一覧取得処理
    category_response = services.list_equipment_categories(db)

    # 2. 戻り値を設定
    return category_response


@api_router.get("/equipments/{equipment_id}", response_model=EquipmentResponse)
def get_equipment(
    equipment_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_active_user)],
) -> EquipmentResponse:
    """
    備品取得

    設計書：設計書/サーバー処理（main）/備品管理/備品取得

    【処理概要】
    - 備品1件の詳細と現在の貸出状況を取得する（備品詳細画面用）。
    - 無効化済みは、管理者以外には存在しないものとして扱う。

    【パラメータ】
    - equipment_id (int) : 備品内部ID（1以上）
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効ユーザー。初期パスワード変更済み）

    【戻り値】
    - equipment_response (EquipmentResponse) : 備品

    【例外処理】
    - HTTPException(404) : 備品が存在しない場合、または無効化済みで呼び出し元が管理者でない場合
    - HTTPException(401) : 認証エラー

    【処理フロー】
    1. 備品取得処理（services.get_equipment_detail）
    2. 戻り値を設定
    """
    # 1. 備品取得処理
    equipment_response = services.get_equipment_detail(db, authenticated_user, equipment_id)

    # 2. 戻り値を設定
    return equipment_response


@api_router.get("/equipments/{equipment_id}/reservations", response_model=ReservationListResponse)
def list_reservations(
    equipment_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_active_user)],
) -> ReservationListResponse:
    """
    予約状況取得

    設計書：設計書/サーバー処理（main）/備品管理/予約状況取得

    【処理概要】
    - 備品の承認済み・貸出中の期間を返し、利用者が空き期間を把握できるようにする。
    - 借用者氏名は管理者にのみ返す。

    【パラメータ】
    - equipment_id (int) : 備品内部ID（1以上）
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効ユーザー。初期パスワード変更済み）

    【戻り値】
    - reservation_response (ReservationListResponse) : 予約状況（開始日の昇順。0件は空配列）

    【例外処理】
    - HTTPException(404) : 備品が存在しない場合、または無効化済みで呼び出し元が管理者でない場合
    - HTTPException(401) : 認証エラー

    【処理フロー】
    1. 予約状況取得処理（services.get_equipment_reservations）
    2. 戻り値を設定
    """
    # 1. 予約状況取得処理
    reservation_response = services.get_equipment_reservations(db, authenticated_user, equipment_id)

    # 2. 戻り値を設定
    return reservation_response


@api_router.post("/admin/equipments", response_model=EquipmentResponse, status_code=status.HTTP_201_CREATED)
def create_equipment_endpoint(
    request: EquipmentCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_admin_user)],
) -> EquipmentResponse:
    """
    備品登録

    設計書：設計書/サーバー処理（main）/備品管理/備品登録

    【処理概要】
    - 管理者が備品を1点登録する。資産番号の重複を検証し、有効な備品として登録する。

    【パラメータ】
    - request (EquipmentCreateRequest) : 備品登録リクエスト
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）

    【戻り値】
    - equipment_response (EquipmentResponse) : 登録した備品（201）

    【例外処理】
    - HTTPException(409) : 資産番号の重複
    - HTTPException(401・403) : 認証・権限エラー

    【処理フロー】
    1. 備品登録処理（services.register_equipment）
    2. 戻り値を設定
    """
    # 1. 備品登録処理
    equipment_response = services.register_equipment(db, request)

    # 2. 戻り値を設定
    return equipment_response


@api_router.post("/admin/equipments/import", response_model=CsvImportResponse, status_code=status.HTTP_201_CREATED)
def import_equipments(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_admin_user)],
) -> CsvImportResponse:
    """
    備品CSV一括登録

    設計書：設計書/サーバー処理（main）/備品管理/備品CSV一括登録

    【処理概要】
    - 管理者が、CSVファイルから備品を一括登録する。全件成功または全件失敗とする。
    - アップロードされたファイル名・Content-Typeは信頼せず、処理に使用しない。

    【パラメータ】
    - file (UploadFile) : CSVファイル（multipart/form-dataのファイルパート。UTF-8・最大5MB）
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）

    【戻り値】
    - import_response (CsvImportResponse) : 登録件数（201）

    【例外処理】
    - HTTPException(400) : ファイルサイズが上限（5MB）を超える・ファイルが空・CSVの形式や件数の誤り
    - CsvImportError : 行ごとの検証エラー（専用の例外ハンドラーがCSVエラーレスポンスの400へ変換）
    - HTTPException(409) : 検証後の同時登録により資産番号が重複した場合
    - HTTPException(401・403) : 認証・権限エラー

    【処理フロー】
    1. ファイルの受け取りとサイズ検証（最大サイズ＋1バイトまで読み込む）
    2. CSVの検証と一括登録（services.import_equipments_csv）
    3. 戻り値を設定
    """
    # 1. ファイルの受け取りとサイズ検証（全体をメモリに読み込む前に上限を超えるかだけを判定する）
    content = file.file.read(CSV_MAX_BYTES + 1)
    if len(content) > CSV_MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ファイルサイズが上限（5MB）を超えています")
    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSVファイルが空です")

    # 2. CSVの検証と一括登録
    import_response = services.import_equipments_csv(db, content)

    # 3. 戻り値を設定
    return import_response


@api_router.put("/admin/equipments/{equipment_id}", response_model=EquipmentResponse)
def update_equipment_endpoint(
    equipment_id: Annotated[int, Path(ge=1)],
    request: EquipmentUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_admin_user)],
) -> EquipmentResponse:
    """
    備品編集

    設計書：設計書/サーバー処理（main）/備品管理/備品編集

    【処理概要】
    - 管理者が備品の内容を更新する。有効フラグによる無効化・再有効化を含む。資産番号は変更しない。

    【パラメータ】
    - equipment_id (int) : 備品内部ID（1以上）
    - request (EquipmentUpdateRequest) : 備品編集リクエスト（全項目を指定する全置換）
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）

    【戻り値】
    - equipment_response (EquipmentResponse) : 更新後の備品

    【例外処理】
    - HTTPException(404) : 備品が存在しない場合
    - HTTPException(400) : 申請中・承認済み・貸出中の申請がある備品を無効化する場合
    - HTTPException(401・403) : 認証・権限エラー

    【処理フロー】
    1. 備品編集処理（services.update_equipment_admin）
    2. 戻り値を設定
    """
    # 1. 備品編集処理
    equipment_response = services.update_equipment_admin(db, equipment_id, request)

    # 2. 戻り値を設定
    return equipment_response


app.include_router(api_router)
