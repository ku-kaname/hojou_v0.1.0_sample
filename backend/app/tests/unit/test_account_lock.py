"""
単体テスト：アカウントロック（ロック判定・ロック解除・失敗回数の記録）

テスト仕様書：単体テスト仕様書/backend/認証・ユーザー管理/アカウントロック
設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ログイン
テスト対象ファイル：backend/app/services.py（_is_locked・_release_expired_lock・_record_password_failure）

【テストの考え方】
- データベースは使わない。ユーザーは必要な項目だけを持つ簡易オブジェクトで代用する。
- 「ログイン状態更新」（データベースへの保存）は偽物（モック）に差し替え、
  どんな値で呼ばれたか（または呼ばれなかったか）だけを確認する。
- コメントの「項番」は、単体テスト仕様書の項番を表す。
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import services

# テストで「今」として使う固定の現在日時
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
ONE_MINUTE = timedelta(minutes=1)


def _make_user(failed_login_count: int = 0, locked_until: datetime | None = None) -> SimpleNamespace:
    """ロック判定に必要な項目（失敗回数・ロック解除日時）だけを持つユーザーを作る"""
    return SimpleNamespace(failed_login_count=failed_login_count, locked_until=locked_until)


@pytest.fixture
def update_login_state(monkeypatch) -> MagicMock:
    """ログイン状態更新（データベース保存）を偽物に差し替え、呼び出し内容を確認できるようにする"""
    mock = MagicMock()
    monkeypatch.setattr(services.crud, "update_login_state", mock)
    return mock


# ---------------- _is_locked（項番1〜4） ----------------


def test_is_locked_no_lock_time():
    """項番1：ロック解除日時がなければロック中ではない"""
    user = _make_user(locked_until=None)
    assert services._is_locked(user, NOW) is False


def test_is_locked_lock_time_in_future():
    """項番2：ロック解除日時が1分後（まだ先）ならロック中"""
    user = _make_user(locked_until=NOW + ONE_MINUTE)
    assert services._is_locked(user, NOW) is True


def test_is_locked_lock_time_equals_now():
    """項番3：ロック解除日時がちょうど現在日時なら、ロック中ではない（「より後」のときだけロック中）"""
    user = _make_user(locked_until=NOW)
    assert services._is_locked(user, NOW) is False


def test_is_locked_lock_time_in_past():
    """項番4：ロック解除日時が1分前（過ぎている）ならロック中ではない"""
    user = _make_user(locked_until=NOW - ONE_MINUTE)
    assert services._is_locked(user, NOW) is False


# ---------------- _release_expired_lock（項番5〜8） ----------------


def test_release_expired_lock_no_lock_time(update_login_state):
    """項番5：ロック解除日時がなければ何も更新しない"""
    user = _make_user(failed_login_count=3, locked_until=None)
    services._release_expired_lock(None, user, NOW)
    update_login_state.assert_not_called()


def test_release_expired_lock_still_locked(update_login_state):
    """項番6：まだロック中（解除日時が1分後）なら更新せず、失敗回数・ロックを維持する"""
    user = _make_user(failed_login_count=5, locked_until=NOW + ONE_MINUTE)
    services._release_expired_lock(None, user, NOW)
    update_login_state.assert_not_called()


def test_release_expired_lock_at_boundary(update_login_state):
    """項番7：ロック解除日時がちょうど現在日時なら解除する（失敗回数0・ロックなしに更新）"""
    user = _make_user(failed_login_count=5, locked_until=NOW)
    services._release_expired_lock("db", user, NOW)
    update_login_state.assert_called_once_with("db", user, 0, None, NOW)


def test_release_expired_lock_expired(update_login_state):
    """項番8：ロック期間が過ぎている（解除日時が1分前）なら解除する"""
    user = _make_user(failed_login_count=5, locked_until=NOW - ONE_MINUTE)
    services._release_expired_lock("db", user, NOW)
    update_login_state.assert_called_once_with("db", user, 0, None, NOW)


# ---------------- _record_password_failure（項番9〜13） ----------------


def test_record_password_failure_first_time(update_login_state):
    """項番9：初回の失敗は回数が1になり、ロックはかからない"""
    user = _make_user(failed_login_count=0, locked_until=None)
    services._record_password_failure("db", user, NOW)
    update_login_state.assert_called_once_with("db", user, 1, None, NOW)


def test_record_password_failure_below_limit(update_login_state):
    """項番10：失敗が3回から4回になってもロックはかからない（上限5回未満）"""
    user = _make_user(failed_login_count=3, locked_until=None)
    services._record_password_failure("db", user, NOW)
    update_login_state.assert_called_once_with("db", user, 4, None, NOW)


def test_record_password_failure_reaches_limit(update_login_state):
    """項番11：失敗が5回目に達したら、現在日時の15分後までロックする"""
    user = _make_user(failed_login_count=4, locked_until=None)
    services._record_password_failure("db", user, NOW)
    expected_lock_until = NOW + timedelta(minutes=15)
    update_login_state.assert_called_once_with("db", user, 5, expected_lock_until, NOW)


def test_record_password_failure_over_limit(update_login_state):
    """項番12：上限を超えた失敗（5回→6回）でも、ロック解除日時を現在日時の15分後に更新する"""
    user = _make_user(failed_login_count=5, locked_until=NOW - ONE_MINUTE)
    services._record_password_failure("db", user, NOW)
    expected_lock_until = NOW + timedelta(minutes=15)
    update_login_state.assert_called_once_with("db", user, 6, expected_lock_until, NOW)


def test_record_password_failure_keeps_existing_lock_time(update_login_state):
    """項番13：上限未満の失敗では、既にあるロック解除日時をそのまま維持する"""
    existing_lock_until = NOW + timedelta(minutes=5)
    user = _make_user(failed_login_count=1, locked_until=existing_lock_until)
    services._record_password_failure("db", user, NOW)
    update_login_state.assert_called_once_with("db", user, 2, existing_lock_until, NOW)
