"""
結合テスト：共通（テーブル制約・通知登録・通知生成・ヘルスチェック・認証）

設計書：設計書/テーブル定義（models）、設計書/CRUD/共通/通知登録、設計書/サーバー処理（main）/共通/
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

import jwt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app import auth, crud, services
from app.database import get_db
from app.main import app
from app.models import Equipment, LoanRequest, Notification, User
from app.schemas import AuthenticatedUser
from app.services import get_now

TODAY = date(2026, 9, 26)


@app.get("/api/_test_protected")
def _protected_endpoint(user: Annotated[AuthenticatedUser, Depends(auth.get_current_user)]) -> dict[str, int]:
    """認証検証用のテスト専用エンドポイント"""
    return {"id": user.id}


@pytest.fixture()
def client(db):
    """テスト用DBセッションを使うAPIクライアント"""
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create_user(db, login_id="user1") -> User:
    user = User(login_id=login_id, name="山田", password_hash="x")
    db.add(user)
    db.flush()
    return user


def _create_equipment(db, asset="A-1") -> Equipment:
    equipment = Equipment(asset_number=asset, name="PC", category="PC")
    db.add(equipment)
    db.flush()
    return equipment


def _count_rows(db) -> tuple[int, int, int]:
    """ユーザー・備品・貸出申請の件数を返す（データが変更されていないかの確認用）"""
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in (User, Equipment, LoanRequest))


def _create_loan(db, user, equipment, start, due, status) -> LoanRequest:
    loan = LoanRequest(
        equipment_id=equipment.id,
        requester_id=user.id,
        start_date=start,
        due_date=due,
        purpose="test",
        status=status,
        requested_at=get_now(),
    )
    db.add(loan)
    db.flush()
    return loan


def _count_notifications(db) -> int:
    return db.scalar(select(func.count()).select_from(Notification))


def test_user_defaults_and_get_user_by_id(db):
    """
    【結合テスト項番1】正常系 - ユーザー登録時の既定値と、
      内部IDでのユーザー取得（行ロック指定を含む）
    前提条件：
    ・テスト用DB起動済み
    ・ユーザーを役割・有効フラグ・初期パスワード変更フラグを指定せず登録済み
    入力値：
    ・内部IDで取得
    ・存在しない内部ID 999999 で取得
    ・行ロック指定（for_update）で取得
    想定結果：
    ・役割=general、有効=true、初期パスワード変更要=true、トークン世代=0
    ・取得結果なし（None）
    ・ユーザーを取得できる
    """
    user = _create_user(db)
    found = crud.get_user_by_id(db, user.id)
    assert found is not None
    assert (found.role, found.is_active, found.must_change_password, found.token_generation) == (
        "general",
        True,
        True,
        0,
    )
    assert crud.get_user_by_id(db, 999999) is None
    assert crud.get_user_by_id(db, user.id, for_update=True) is not None


def test_login_id_format_check(db):
    """
    【結合テスト項番2】異常系 - ログインIDに使えない文字（空白・記号）を含むと、DBが登録を拒否する
    前提条件：
    ・テスト用DB起動済み
    入力値：ログインID「bad id!」のユーザーを登録
    想定結果：IntegrityError（DBのCHECK制約）
    """
    db.add(User(login_id="bad id!", name="x", password_hash="x"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_overlapping_approved_loans_rejected(db):
    """
    【結合テスト項番3】異常系 - 同じ備品の承認済みの期間が重なる登録を、
      DBが拒否する（終了日と開始日が同日でも重複）
    前提条件：
    ・テスト用DB起動済み
    ・備品と、承認済みの申請（10/1〜10/5）が登録済み
    入力値：承認済みの申請（10/5〜10/8）を登録
    想定結果：IntegrityError（DBの重複防止制約）
    """
    user = _create_user(db)
    equipment = _create_equipment(db)
    _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "approved")
    with pytest.raises(IntegrityError):
        _create_loan(db, user, equipment, date(2026, 10, 5), date(2026, 10, 8), "approved")


def test_overlapping_requested_loans_allowed(db):
    """
    【結合テスト項番4】正常系 - 申請中の期間は重なっていても登録できる
    前提条件：
    ・テスト用DB起動済み
    ・備品と、申請中の申請（10/1〜10/5）が登録済み
    入力値：申請中の申請（10/3〜10/8）を登録
    想定結果：エラーなく登録できる
    """
    user = _create_user(db)
    equipment = _create_equipment(db)
    _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "requested")
    _create_loan(db, user, equipment, date(2026, 10, 3), date(2026, 10, 8), "requested")


def test_adjacent_period_allowed(db):
    """
    【結合テスト項番5】境界値 - 承認済みの期間が隣り合う（翌日開始）登録はできる
    前提条件：
    ・テスト用DB起動済み
    ・備品と、承認済みの申請（10/1〜10/5）が登録済み
    入力値：承認済みの申請（10/6〜10/8）を登録
    想定結果：エラーなく登録できる
    """
    user = _create_user(db)
    equipment = _create_equipment(db)
    _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "approved")
    _create_loan(db, user, equipment, date(2026, 10, 6), date(2026, 10, 8), "approved")


def test_invalid_period_rejected(db):
    """
    【結合テスト項番6】異常系 - 返却予定日が開始日より前の登録を、DBが拒否する
    前提条件：
    ・テスト用DB起動済み
    ・ユーザーと備品が登録済み
    入力値：開始日=10/5・返却予定日=10/1 の申請を登録
    想定結果：IntegrityError（DBのCHECK制約）
    """
    user = _create_user(db)
    equipment = _create_equipment(db)
    with pytest.raises(IntegrityError):
        _create_loan(db, user, equipment, date(2026, 10, 5), date(2026, 10, 1), "requested")


def test_create_notification_normal_allows_duplicates(db):
    """
    【結合テスト項番7】正常系 - 随時通知（承認など）は同じ内容でも重複して登録できる
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー・備品・申請中の申請が登録済み
    入力値：種類「承認」・同じ申請・同じ通知日で2回登録
    想定結果：
    ・2回とも True を返す
    ・通知が2件
    """
    user = _create_user(db)
    equipment = _create_equipment(db)
    loan = _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "requested")
    assert crud.create_notification(db, user.id, "approved", loan.id, TODAY) is True
    assert crud.create_notification(db, user.id, "approved", loan.id, TODAY) is True
    assert _count_notifications(db) == 2


def test_create_notification_daily_is_idempotent(db):
    """
    【結合テスト項番8】境界値 - 日次通知（期限超過）は同じ日に重複せず、日が変われば登録できる
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー・備品・貸出中の申請が登録済み
    入力値：
    ・種類「期限超過」・通知日=9/26 で登録
    ・同じ内容でもう一度登録
    ・通知日=9/27 で登録
    想定結果：
    ・True
    ・False（登録されない）
    ・True
    """
    user = _create_user(db)
    equipment = _create_equipment(db)
    loan = _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "lent")
    assert crud.create_notification(db, user.id, "overdue", loan.id, TODAY) is True
    assert crud.create_notification(db, user.id, "overdue", loan.id, TODAY) is False
    assert crud.create_notification(db, user.id, "overdue", loan.id, date(2026, 9, 27)) is True
    assert _count_notifications(db) == 2


def test_create_notifications_deduplicates_recipients(db):
    """
    【結合テスト項番9】正常系 - 宛先が重複していても、同じ宛先へは1件だけ通知を作る
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー2人（user1・user2）と、申請中の申請が登録済み
    入力値：宛先 [user1, user2, user1] で種類「新規申請」の通知を作成
    想定結果：通知が2件（各宛先に1件）
    """
    first = _create_user(db, "user1")
    second = _create_user(db, "user2")
    equipment = _create_equipment(db)
    loan = _create_loan(db, first, equipment, date(2026, 10, 1), date(2026, 10, 5), "requested")
    services.create_notifications(db, [first.id, second.id, first.id], "new_request", loan.id, TODAY)
    assert _count_notifications(db) == 2


def test_health_check_ok(client):
    """
    【結合テスト項番10】正常系 - DBに接続できれば正常を返す
    前提条件：
    ・テスト用DB起動済み
    入力値：認証なしで GET /api/health
    想定結果：
    ・HTTP 200
    ・{"status": "ok"}
    """
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_token_returns_401(client):
    """
    【結合テスト項番12】異常系 - トークンなしは401
    前提条件：
    ・テスト用DB起動済み
    入力値：トークンなしで GET
    想定結果：
    ・HTTP 401
    ・detail が "資格情報を検証できませんでした"
    ・WWW-Authenticate ヘッダーが Bearer
    """
    response = client.get("/api/_test_protected")
    assert response.status_code == 401
    assert response.json() == {"detail": "資格情報を検証できませんでした"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_valid_token_returns_user(client, db):
    """
    【結合テスト項番13】正常系 - 有効なトークンならログイン中のユーザーIDを返す
    前提条件：
    ・テスト用DB起動済み
    ・有効なユーザーが登録済み。そのユーザーのトークンを発行済み
    入力値：Authorization: Bearer <有効なトークン>
    想定結果：
    ・HTTP 200
    ・ユーザーIDが一致する
    """
    user = _create_user(db)
    token = auth.create_access_token(user.id, user.token_generation, get_now())
    response = client.get("/api/_test_protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"id": user.id}


def test_generation_mismatch_inactive_and_unknown_user_return_same_401(client, db):
    """
    【結合テスト項番14】セキュリティ - トークン世代の不一致・存在しないユーザー・無効化ユーザーはす
      べて同じ401を返す（原因を推測させない）
    前提条件：
    ・テスト用DB起動済み
    ・ユーザーが登録済み。世代が古い（+1）
        トークン・存在しないユーザーのトークン・ユーザー無効化後のトークンを用意
    入力値：それぞれのトークンで GET
    想定結果：
    ・3件とも HTTP 401
    ・3件とも detail が "資格情報を検証できませんでした"
    """
    user = _create_user(db)
    stale_token = auth.create_access_token(user.id, user.token_generation + 1, get_now())
    unknown_token = auth.create_access_token(999999, 0, get_now())
    valid_token = auth.create_access_token(user.id, user.token_generation, get_now())
    user.is_active = False
    db.flush()
    responses = [
        client.get("/api/_test_protected", headers={"Authorization": f"Bearer {token}"})
        for token in (stale_token, unknown_token, valid_token)
    ]
    assert {response.status_code for response in responses} == {401}
    assert {response.json()["detail"] for response in responses} == {"資格情報を検証できませんでした"}


def test_expired_and_forged_tokens_return_401(client, db):
    """
    【結合テスト項番15】セキュリティ - 期限切れ・別の鍵で作った偽造・形式不正のトークンは401
    前提条件：
    ・テスト用DB起動済み
    ・ユーザーが登録済み。9時間前に発行したトークン・別の秘密鍵で署名したトークン・文字列「not.a.jwt」
        を用意
    入力値：それぞれのトークンで GET
    想定結果：3件とも HTTP 401
    """
    user = _create_user(db)
    old_now = datetime.now(UTC) - timedelta(hours=9)
    expired_token = auth.create_access_token(user.id, user.token_generation, old_now)
    forged_token = jwt.encode(
        {"sub": str(user.id), "gen": 0, "exp": datetime.now(UTC) + timedelta(hours=1)},
        "another-secret-key-0123456789-abcdefghijkl",
        algorithm="HS256",
    )
    for token in (expired_token, forged_token, "not.a.jwt"):
        response = client.get("/api/_test_protected", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


def test_health_check_returns_503_when_db_unavailable(client, db, monkeypatch):
    """
    【結合テスト項番11】異常系 - DBに接続できなければ503を返し、接続エラーの詳細は返さない
    前提条件：
    ・テスト用DB起動済み
    ・DBへの問い合わせが接続エラー（connection refused）を出すよう差し替える
    入力値：GET /api/health
    想定結果：
    ・HTTP 503
    ・detail が "サービスを利用できません"（接続エラーの詳細は含まない）
    """

    def raise_connection_error(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(db, "execute", raise_connection_error)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "サービスを利用できません"}


def test_unexpected_error_returns_500_without_details(db):
    """
    【結合テスト項番17】セキュリティ - 想定外の例外は500を返し、内部の詳細を応答に含めない
    前提条件：
    ・テスト用DB起動済み
    ・呼ばれると例外「secret internal detail」を出すエンドポイントを追加
    入力値：GET /api/_test_error
    想定結果：
    ・HTTP 500
    ・detail が "サーバーエラーが発生しました"
    ・応答本文に「secret」が含まれない
    """

    def raise_unexpected_error():
        raise ValueError("secret internal detail")

    app.dependency_overrides[get_db] = lambda: db
    app.add_api_route("/api/_test_error", raise_unexpected_error)
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/api/_test_error")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500
    assert response.json() == {"detail": "サーバーエラーが発生しました"}
    assert "secret" not in response.text


def test_auth_failure_is_logged_without_token(client, caplog):
    """
    【結合テスト項番16】セキュリティ - 認証失敗はログに残すが、トークンの値は残さない
    前提条件：
    ・テスト用DB起動済み
    入力値：不正なトークン「secret-token-value」で GET（ログレベルWARNINGで記録を取得）
    想定結果：
    ・ログに「認証・認可エラー」が含まれる
    ・ログにトークンの値が含まれない
    """
    with caplog.at_level("WARNING"):
        client.get("/api/_test_protected", headers={"Authorization": "Bearer secret-token-value"})
    assert "認証・認可エラー" in caplog.text
    assert "secret-token-value" not in caplog.text


@pytest.mark.parametrize(
    ("method", "path", "role", "body"),
    [
        ("post", "/api/auth/login", None, {}),
        ("put", "/api/users/me/password", "general", {}),
        ("post", "/api/admin/users", "admin", {}),
        ("post", "/api/admin/equipments", "admin", {}),
        ("post", "/api/loan-requests", "general", {}),
        ("post", "/api/loan-requests", "general", {"equipment_id": "abc"}),
    ],
)
def test_required_fields_missing_or_wrong_type_returns_422(client, db, method, path, role, body):
    """
    【結合テスト項番18】異常系 - 必須項目の欠落・型の不一致は422（本文が空・型違い）
    前提条件：
    ・テスト用DB起動済み
    ・管理者と一般ユーザーが登録済み・トークン取得済み
    入力値：
    ・POST /api/auth/login を本文 {} で呼ぶ
    ・PUT /api/users/me/password を {} で呼ぶ（一般ユーザー）
    ・POST /api/admin/users を {} で呼ぶ（管理者）
    ・POST /api/admin/equipments を {} で呼ぶ（管理者）
    ・POST /api/loan-requests を {} で呼ぶ（一般ユーザー）
    ・POST /api/loan-requests を equipment_id="abc"（型違い）で呼ぶ
    想定結果：6件とも HTTP 422（登録・更新は行われない）
    """
    # 必須項目が無い・型が違う入力は、処理に入る前に422で拒否される（データは変更されない）
    headers = {}
    if role is not None:
        user = User(
            login_id=f"{role}1",
            name="山田",
            password_hash=auth.hash_password("Password123"),
            role=role,
            must_change_password=False,
        )
        db.add(user)
        db.flush()
        token = auth.create_access_token(user.id, user.token_generation, get_now())
        headers = {"Authorization": f"Bearer {token}"}
    # 呼び出し前のデータ件数を控えておく
    counts_before = _count_rows(db)
    response = getattr(client, method)(path, json=body, headers=headers)
    assert response.status_code == 422
    # 拒否されたので、ユーザー・備品・貸出申請の件数が変わっていない（登録・更新されていない）
    assert _count_rows(db) == counts_before


@pytest.mark.parametrize(
    ("method", "path", "role"),
    [
        ("post", "/api/admin/loan-requests/1/lend", "admin"),
        ("post", "/api/admin/loan-requests/1/return", "admin"),
        ("get", "/api/admin/loan-history", "admin"),
        ("get", "/api/admin/loan-history/export", "admin"),
        ("get", "/api/admin/loan-requests/overdue", "admin"),
        ("get", "/api/notifications", "general"),
        ("get", "/api/notifications/summary", "general"),
        ("post", "/api/notifications/1/read", "general"),
        ("post", "/api/notifications/read-all", "general"),
    ],
)
def test_user_who_must_change_password_is_rejected(client, db, method, path, role):
    """
    【結合テスト項番19】異常系 - 初期パスワード未変更のユーザーは、
      貸出・返却・履歴検索・履歴CSV出力・期限超過一覧・通知の全9エンドポイントを利用できない
    前提条件：
    ・テスト用DB起動済み
    ・初期パスワード変更要（must_change_password=true）
        の管理者・一般ユーザーが登録済み・トークン取得済み
    入力値：
    ・管理者で POST /api/admin/loan-requests/1/lend・/return、GET /api/admin/loan-history・/export、
        GET /api/admin/loan-requests/overdue
    ・一般ユーザーで GET /api/notifications・/summary、POST /api/notifications/1/read・/read-all
    想定結果：
    ・9件とも HTTP 403
    ・detail が "初期パスワードの変更が必要です"
    """
    # 初期パスワードを変更していないユーザーは、パスワード変更以外の機能を使えない（403）
    user = User(
        login_id=f"{role}1",
        name="山田",
        password_hash=auth.hash_password("Password123"),
        role=role,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    token = auth.create_access_token(user.id, user.token_generation, get_now())
    response = getattr(client, method)(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json() == {"detail": "初期パスワードの変更が必要です"}
