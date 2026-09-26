"""
単体テスト：認証・認可（パスワード・トークン）

設計書：設計書/認証・認可（auth）/
"""

from datetime import UTC, datetime

import jwt
import pytest
from fastapi import HTTPException

from app import auth
from app.schemas import AuthenticatedUser


def _make_user(role: str = "general", must_change_password: bool = False) -> AuthenticatedUser:
    return AuthenticatedUser(id=1, login_id="user1", name="山田", role=role, must_change_password=must_change_password)


def test_hash_and_verify_password():
    password_hash = auth.hash_password("correct-password")
    assert password_hash.startswith("$argon2id$")
    assert len(password_hash) <= 255
    assert auth.verify_password("correct-password", password_hash) is True
    assert auth.verify_password("wrong-password", password_hash) is False


def test_verify_password_returns_false_when_user_missing():
    assert auth.verify_password("any-password", None) is False


def test_verify_password_returns_false_for_malformed_hash():
    assert auth.verify_password("any-password", "not-a-hash") is False


def test_create_access_token_payload():
    now = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)
    token = auth.create_access_token(user_id=7, token_generation=3, now=now)
    payload = jwt.decode(token, auth._get_jwt_secret_key(), algorithms=["HS256"], options={"verify_exp": False})
    assert payload["sub"] == "7"
    assert payload["gen"] == 3
    assert payload["exp"] - payload["iat"] == 8 * 3600
    assert set(payload) == {"sub", "gen", "iat", "exp"}


def test_jwt_secret_key_too_short(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "short")
    with pytest.raises(RuntimeError, match="JWT秘密鍵が未設定または短すぎます"):
        auth.validate_jwt_settings()


def test_jwt_secret_key_missing(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        auth.create_access_token(1, 0, datetime.now(UTC))


def test_get_current_active_user_rejects_initial_password():
    with pytest.raises(HTTPException) as error:
        auth.get_current_active_user(_make_user(must_change_password=True))
    assert error.value.status_code == 403
    assert error.value.detail == "初期パスワードの変更が必要です"


def test_get_current_active_user_passes():
    user = _make_user()
    assert auth.get_current_active_user(user) is user


def test_get_current_admin_user_rejects_general():
    with pytest.raises(HTTPException) as error:
        auth.get_current_admin_user(_make_user(role="general"))
    assert error.value.status_code == 403
    assert error.value.detail == "この操作を行う権限がありません"


def test_get_current_admin_user_passes_admin():
    user = _make_user(role="admin")
    assert auth.get_current_admin_user(user) is user
