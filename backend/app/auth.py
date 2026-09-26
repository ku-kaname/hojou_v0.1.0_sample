"""
認証・認可

【概要】
パスワードのハッシュ化・検証、アクセストークン（JWT）の発行、および各エンドポイントで使う認証・認可の検証
（現在ユーザー取得・有効ユーザー検証・管理者権限検証）を提供する。

設計書：設計書/認証・認可（auth）/
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.models import Role
from app.schemas import AuthenticatedUser

logger = logging.getLogger("app.auth")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8
JWT_SECRET_MIN_LENGTH = 32

# Argon2idでハッシュ化・検証を行う（pwdlibの推奨パラメーター）
_password_hasher = PasswordHash((Argon2Hasher(),))

# ユーザーが存在しない場合の検証に使うダミーハッシュ（起動時に1度だけ生成）。
# 処理時間の差からユーザーの有無を推測されないようにするために使う
_DUMMY_PASSWORD_HASH = _password_hasher.hash("dummy-password-for-timing-equalization")

# auto_error=False：トークン未指定時の応答を、標準の英語メッセージではなく
# 他の認証失敗と同一の日本語メッセージにそろえるため
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

_CREDENTIALS_ERROR_MESSAGE = "資格情報を検証できませんでした"


def _get_jwt_secret_key() -> str:
    """
    JWT秘密鍵取得（内部処理）

    【処理概要】
    - 環境変数`JWT_SECRET_KEY`から秘密鍵を取得し、未設定・短すぎる場合はエラーとする（鍵の値はエラー内容に含めない）。

    【戻り値】
    - secret_key (str) : JWT秘密鍵

    【例外処理】
    - RuntimeError : `JWT_SECRET_KEY`が未設定、または32文字未満の場合
    """
    secret_key = os.environ.get("JWT_SECRET_KEY", "")
    if len(secret_key) < JWT_SECRET_MIN_LENGTH:
        raise RuntimeError("JWT秘密鍵が未設定または短すぎます")
    return secret_key


def validate_jwt_settings() -> None:
    """
    JWT設定検証（起動時に呼び出す）

    【処理概要】
    - 秘密鍵の設定不備を、リクエストを受け付ける前（起動時）に検出する。

    【例外処理】
    - RuntimeError : `JWT_SECRET_KEY`が未設定、または32文字未満の場合
    """
    _get_jwt_secret_key()


def create_access_token(user_id: int, token_generation: int, now: datetime) -> str:
    """
    トークン発行

    設計書：設計書/認証・認可（auth）/トークン発行

    【処理概要】
    - ユーザーの認証済みを示すアクセストークン（JWT）を発行する。
    - ユーザー内部ID・トークン世代・発行日時・有効期限をペイロードに設定し、署名して返却する。

    【パラメータ】
    - user_id (int) : ユーザー内部ID（1以上）
    - token_generation (int) : ユーザーの現在のトークン世代（0以上）
    - now (datetime) : 現在日時（UTC。呼び出し元が取得した値）

    【戻り値】
    - access_token (str) : 署名済みJWT

    【例外処理】
    - RuntimeError : `JWT_SECRET_KEY`が未設定、または32文字未満の場合（"JWT秘密鍵が未設定または短すぎます"）

    【処理フロー】
    1. ペイロードの作成（個人情報は含めない）
       - sub=ユーザー内部ID文字列、gen=トークン世代、iat=現在日時、exp=現在日時＋8時間
    2. 環境変数JWT_SECRET_KEYを鍵、HS256で署名
    3. 戻り値を設定
    """
    # 1. ペイロードの作成
    expires_at = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "gen": token_generation,
        "iat": now,
        "exp": expires_at,
    }

    # 2. 署名
    secret_key = _get_jwt_secret_key()
    access_token = jwt.encode(payload, secret_key, algorithm=JWT_ALGORITHM)

    # 3. 戻り値を設定
    return access_token


def hash_password(plain_password: str) -> str:
    """
    パスワードハッシュ化

    設計書：設計書/認証・認可（auth）/パスワードハッシュ化

    【処理概要】
    - 平文パスワードを、保存用のハッシュ値（Argon2id・ソルト自動生成）に変換する。
    - 平文パスワード・ハッシュ値をログに出力しない。

    【パラメータ】
    - plain_password (str) : 平文パスワード（8〜128文字。呼び出し元のスキーマで検証済み）

    【戻り値】
    - password_hash (str) : ハッシュ済みパスワード（255桁以内）

    【例外処理】
    - なし

    【処理フロー】
    1. Argon2idでハッシュ化
    2. 戻り値を設定
    """
    password_hash = _password_hasher.hash(plain_password)
    return password_hash


def verify_password(plain_password: str, password_hash: str | None) -> bool:
    """
    パスワード検証

    設計書：設計書/認証・認可（auth）/パスワード検証

    【処理概要】
    - 平文パスワードが、保存済みハッシュと一致するか検証する。
    - ハッシュが無い（ユーザー不在）場合もダミーハッシュで同等の検証を行い、
      処理時間の差からユーザーの存在を推測されないようにする。
    - パスワードをログに出力しない。

    【パラメータ】
    - plain_password (str) : 平文パスワード（1〜128文字）
    - password_hash (str | None) : ハッシュ済みパスワード（ユーザー不在の場合はNone）

    【戻り値】
    - is_valid (bool) : 一致でTrue

    【例外処理】
    - なし（ハッシュの形式が不正な場合も例外を出さず、不一致（False）として扱う）

    【処理フロー】
    1. 検証対象ハッシュの決定（Noneでなければ入力値、Noneならダミーハッシュ）
    2. 検証（ダミーハッシュの場合は、結果によらずFalse）
    3. 戻り値を設定
    """
    # 1. 検証対象ハッシュの決定
    is_dummy = password_hash is None
    target_hash = _DUMMY_PASSWORD_HASH if password_hash is None else password_hash

    # 2. 検証
    try:
        is_matched = _password_hasher.verify(plain_password, target_hash)
    except Exception:
        # ハッシュの形式が不正な場合は不一致として扱う
        return False
    if is_dummy:
        return False

    # 3. 戻り値を設定
    return is_matched


def build_credentials_error() -> HTTPException:
    """認証失敗（401）の例外を作成する。失効・無効化・未登録を区別させないため、常に同一の内容とする"""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_CREDENTIALS_ERROR_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _log_auth_failure(user_id: int | None, result: str) -> None:
    """認証・認可の失敗をサーバーログに記録する（ユーザー内部ID・結果のみ。トークン・入力値は記録しない）"""
    logger.warning("認証・認可エラー: ユーザー内部ID=%s 結果=%s", user_id, result)


def get_current_user(
    token: Annotated[str | None, Depends(_oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthenticatedUser:
    """
    現在ユーザー取得

    設計書：設計書/認証・認可（auth）/現在ユーザー取得

    【処理概要】
    - Bearerトークンを検証し、有効な登録ユーザーであることを確認して、認証済みユーザーを返却する。
    - 初期パスワード未変更のユーザーでも通す（ログアウト・パスワード変更・自分の情報取得用）。

    【パラメータ】
    - token (str | None) : リクエストヘッダーのBearerトークン（未指定は401）
    - db (Session) : DBセッション

    【戻り値】
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー

    【例外処理】
    - HTTPException(401) : 署名不正・期限切れ・形式不正、sub/genが取得できない・整数でない、
      ユーザーが取得できない、有効フラグが偽、トークン世代が不一致（すべて"資格情報を検証できませんでした"）

    【処理フロー】
    1. Bearerトークンの検証（署名・有効期限・必須クレームsub/gen/exp）
    2. 登録ユーザー取得
    3. ユーザー状態の検証（有効フラグ・トークン世代）
    4. 戻り値を設定
    """
    # 1. Bearerトークンの検証（未指定も他の認証失敗と同一の401とする）
    if token is None:
        _log_auth_failure(None, "トークン未指定")
        raise build_credentials_error()
    secret_key = _get_jwt_secret_key()
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "gen", "exp"]},
        )
    except jwt.PyJWTError:
        _log_auth_failure(None, "トークン不正または期限切れ")
        raise build_credentials_error() from None
    try:
        user_id = int(payload["sub"])
        token_generation = payload["gen"]
    except (KeyError, TypeError, ValueError):
        _log_auth_failure(None, "クレーム不正")
        raise build_credentials_error() from None
    # bool（True/False）は整数として扱わない
    if not isinstance(token_generation, int) or isinstance(token_generation, bool):
        _log_auth_failure(user_id, "クレーム不正")
        raise build_credentials_error()

    # 2. 登録ユーザー取得
    user = crud.get_user_by_id(db, user_id)

    # 3. ユーザー状態の検証
    if user is None:
        _log_auth_failure(user_id, "ユーザー未登録")
        raise build_credentials_error()
    if not user.is_active:
        _log_auth_failure(user_id, "ユーザー無効")
        raise build_credentials_error()
    if user.token_generation != token_generation:
        _log_auth_failure(user_id, "トークン世代不一致")
        raise build_credentials_error()

    # 4. 戻り値を設定
    authenticated_user = AuthenticatedUser.model_validate(user)
    return authenticated_user


def get_current_active_user(
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    """
    有効ユーザー検証

    設計書：設計書/認証・認可（auth）/有効ユーザー検証

    【処理概要】
    - 認証済みユーザーのうち、初期パスワードを変更済みのユーザーのみを通す。

    【パラメータ】
    - authenticated_user (AuthenticatedUser) : 現在ユーザー取得の戻り値

    【戻り値】
    - authenticated_user (AuthenticatedUser) : 入力値の認証済みユーザー

    【例外処理】
    - HTTPException(403) : 初回パスワード変更要否が真の場合（"初期パスワードの変更が必要です"）

    【処理フロー】
    1. 初回パスワード変更要否の検証
    2. 戻り値を設定
    """
    if authenticated_user.must_change_password:
        _log_auth_failure(authenticated_user.id, "初期パスワード未変更")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="初期パスワードの変更が必要です")
    return authenticated_user


def get_current_admin_user(
    authenticated_user: Annotated[AuthenticatedUser, Depends(get_current_active_user)],
) -> AuthenticatedUser:
    """
    管理者権限検証

    設計書：設計書/認証・認可（auth）/管理者権限検証

    【処理概要】
    - 有効ユーザー検証を通過したユーザーのうち、管理者ロールのユーザーのみを通す（管理者専用エンドポイント用）。

    【パラメータ】
    - authenticated_user (AuthenticatedUser) : 有効ユーザー検証の戻り値

    【戻り値】
    - authenticated_user (AuthenticatedUser) : 入力値の認証済みユーザー

    【例外処理】
    - HTTPException(403) : ロールが管理者でない場合（"この操作を行う権限がありません"）

    【処理フロー】
    1. ロールの検証
    2. 戻り値を設定
    """
    if authenticated_user.role != Role.ADMIN.value:
        _log_auth_failure(authenticated_user.id, "管理者権限なし")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="この操作を行う権限がありません")
    return authenticated_user
