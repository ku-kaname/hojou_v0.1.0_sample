"""
結合テスト：貸出申請・承認（貸出申請・申請取消・申請承認・申請却下・申請管理者取消・申請一覧・自分の申請取得）

設計書：設計書/サーバー処理（main）/貸出申請・承認/、設計書/CRUD/貸出申請・承認/
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import auth, crud
from app.database import get_db
from app.main import app
from app.models import Equipment, LoanRequest, Notification, NotificationType, User
from app.services import get_now, get_today

PASSWORD = "Password123"


@pytest.fixture()
def client(db):
    """テスト用DBセッションを使うAPIクライアント"""
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_user(db, login_id, role="general", name="山田", is_active=True) -> User:
    user = User(
        login_id=login_id,
        name=name,
        password_hash=auth.hash_password(PASSWORD),
        role=role,
        must_change_password=False,
        is_active=is_active,
    )
    db.add(user)
    db.flush()
    return user


def _headers(user) -> dict[str, str]:
    token = auth.create_access_token(user.id, user.token_generation, get_now())
    return {"Authorization": f"Bearer {token}"}


def _make_equipment(db, asset_number, is_active=True) -> Equipment:
    equipment = Equipment(asset_number=asset_number, name="ノートPC", category="PC", is_active=is_active)
    db.add(equipment)
    db.flush()
    return equipment


def _make_loan(db, equipment, requester, status, start, due, requested_at=None) -> LoanRequest:
    loan = LoanRequest(
        equipment_id=equipment.id,
        requester_id=requester.id,
        start_date=start,
        due_date=due,
        purpose="test",
        status=status,
        requested_at=requested_at or get_now(),
    )
    db.add(loan)
    db.flush()
    return loan


def _notifications(db, loan_id) -> list[Notification]:
    statement = select(Notification).where(Notification.loan_request_id == loan_id).order_by(Notification.id)
    return list(db.execute(statement).scalars())


def _apply(client, user, equipment, start, due, purpose="出張用"):
    body = {
        "equipment_id": equipment.id,
        "start_date": start.isoformat(),
        "due_date": due.isoformat(),
        "purpose": purpose,
    }
    return client.post("/api/loan-requests", headers=_headers(user), json=body)


# ---- 認証・認可 ----


def test_loan_request_endpoints_require_authentication(client):
    """
    【結合テスト項番1】異常系 - 認証なしは401（貸出申請・承認の全8エンドポイント共通）
    前提条件：
    ・テスト用DB起動済み
    入力値：トークンなしで POST /api/loan-requests・GET /api/loan-requests/me・GET /api/loan-request
      s/me/1・POST /api/loan-requests/1/cancel・GET /api/admin/loan-requests・POST /api/admin/loan-r
      equests/1/approve・/reject・/admin-cancel
    想定結果：8件とも HTTP 401
    """
    for method, path in (
        ("post", "/api/loan-requests"),
        ("get", "/api/loan-requests/me"),
        ("get", "/api/loan-requests/me/1"),
        ("post", "/api/loan-requests/1/cancel"),
        ("get", "/api/admin/loan-requests"),
        ("post", "/api/admin/loan-requests/1/approve"),
        ("post", "/api/admin/loan-requests/1/reject"),
        ("post", "/api/admin/loan-requests/1/admin-cancel"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 401


def test_admin_loan_request_endpoints_reject_general_user(db, client):
    """
    【結合テスト項番20】異常系 - 一般ユーザーは管理者用の申請一覧・承認・却下・管理者取消を使えない
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    入力値：一般ユーザーで GET /api/admin/loan-requests、POST /api/admin/loan-requests/1/approve・/r
      eject・/admin-cancel（理由付き）
    想定結果：
    ・4件とも HTTP 403
    ・detail が "この操作を行う権限がありません"
    """
    user = _make_user(db, "taro")
    reason = {"reason": "理由"}
    responses = [
        client.get("/api/admin/loan-requests", headers=_headers(user)),
        client.post("/api/admin/loan-requests/1/approve", headers=_headers(user)),
        client.post("/api/admin/loan-requests/1/reject", headers=_headers(user), json=reason),
        client.post("/api/admin/loan-requests/1/admin-cancel", headers=_headers(user), json=reason),
    ]
    for response in responses:
        assert response.status_code == 403
        assert response.json() == {"detail": "この操作を行う権限がありません"}


def test_loan_request_endpoints_reject_user_who_must_change_password(db, client):
    """
    【結合テスト項番15】異常系 - 初期パスワード未変更のユーザーは利用できない
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro・初期パスワード変更要）が登録済み・トークン取得済み
    入力値：GET /api/loan-requests/me
    想定結果：
    ・HTTP 403
    ・detail が "初期パスワードの変更が必要です"
    """
    user = _make_user(db, "taro")
    user.must_change_password = True
    db.flush()
    response = client.get("/api/loan-requests/me", headers=_headers(user))
    assert response.status_code == 403
    assert response.json() == {"detail": "初期パスワードの変更が必要です"}


# ---- 貸出申請 ----


def test_apply_creates_requested_loan_and_notifies_other_admins(db, client):
    """
    【結合テスト項番2】正常系 - 貸出申請を作成し、有効な管理者全員へ「新規申請」通知を作る
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（太郎）・有効な管理者2人・無効化済みの管理者1人が登録済み。有効な備品（A-1）
        が登録済み
    入力値：太郎が備品A-1を、開始日=今日+3日・返却予定日=今日+5日・用途「出張用」で申請
    想定結果：
    ・HTTP 201
    ・status=requested、equipment_asset_number=A-1、requester_name=太郎、reason が空文字、
        is_overdue=false、overdue_days=0、requested_at が +09:00
    ・通知が有効な管理者2人だけに作られ（無効化済みの管理者には作られない）、種類は「新規申請」
    """
    user = _make_user(db, "taro", name="太郎")
    admin1 = _make_user(db, "admin1", role="admin")
    admin2 = _make_user(db, "admin2", role="admin")
    _make_user(db, "admin3", role="admin", is_active=False)
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, user, equipment, today + timedelta(days=3), today + timedelta(days=5))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "requested"
    assert body["equipment_asset_number"] == "A-1"
    assert body["requester_name"] == "太郎"
    assert body["reason"] == ""
    assert body["is_overdue"] is False
    assert body["overdue_days"] == 0
    assert body["requested_at"].endswith("+09:00")
    notifications = _notifications(db, body["id"])
    assert sorted(n.recipient_id for n in notifications) == sorted([admin1.id, admin2.id])
    assert {n.type for n in notifications} == {NotificationType.NEW_REQUEST.value}


def test_apply_by_admin_does_not_notify_self(db, client):
    """
    【結合テスト項番3】正常系 - 管理者が申請した場合、本人には通知しない
    前提条件：
    ・テスト用DB起動済み
    ・管理者2人（admin1・admin2）が登録済み。有効な備品（A-1）が登録済み
    入力値：admin1 が申請（開始日・返却予定日とも今日）
    想定結果：
    ・HTTP 201
    ・通知は admin2 宛の1件だけ（申請者本人には作られない）
    """
    admin = _make_user(db, "admin1", role="admin")
    other = _make_user(db, "admin2", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, admin, equipment, today, today)
    assert response.status_code == 201
    notifications = _notifications(db, response.json()["id"])
    assert [n.recipient_id for n in notifications] == [other.id]


def test_apply_by_only_admin_creates_no_notification(db, client):
    """
    【結合テスト項番4】正常系 - 有効な管理者が申請者本人だけなら通知は作らない
    前提条件：
    ・テスト用DB起動済み
    ・有効な管理者が1人（admin1）だけ登録済み。有効な備品（A-1）が登録済み
    入力値：admin1 が申請
    想定結果：
    ・HTTP 201
    ・通知は0件
    """
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, admin, equipment, today, today)
    assert response.status_code == 201
    assert _notifications(db, response.json()["id"]) == []


def test_apply_rejects_start_date_in_past(db, client):
    """
    【結合テスト項番5】異常系 - 開始日が過去
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・有効な備品（A-1）が登録済み
    入力値：開始日=昨日・返却予定日=今日
    想定結果：
    ・HTTP 400
    ・detail が "開始日は今日以降を指定してください"
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, user, equipment, today - timedelta(days=1), today)
    assert response.status_code == 400
    assert response.json() == {"detail": "開始日は今日以降を指定してください"}


def test_apply_rejects_due_date_before_start_date(db, client):
    """
    【結合テスト項番6】異常系 - 返却予定日が開始日より前
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・有効な備品（A-1）が登録済み
    入力値：開始日=今日+2日・返却予定日=今日+1日
    想定結果：
    ・HTTP 400
    ・detail が "返却予定日は開始日以降を指定してください"
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, user, equipment, today + timedelta(days=2), today + timedelta(days=1))
    assert response.status_code == 400
    assert response.json() == {"detail": "返却予定日は開始日以降を指定してください"}


def test_apply_rejects_missing_or_inactive_equipment(db, client):
    """
    【結合テスト項番7】異常系 - 存在しない備品・無効化済みの備品は404
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・無効化済みの備品（A-1）が登録済み
    入力値：
    ・無効化済みの備品を申請
    ・存在しない備品ID 999999 を申請
    想定結果：
    ・HTTP 404、detail が "備品が見つかりません"
    ・HTTP 404
    """
    user = _make_user(db, "taro")
    inactive = _make_equipment(db, "A-1", is_active=False)
    today = get_today()
    response = _apply(client, user, inactive, today, today)
    assert response.status_code == 404
    assert response.json() == {"detail": "備品が見つかりません"}
    missing = Equipment(id=999999, asset_number="X", name="x", category="x")
    response = _apply(client, user, missing, today, today)
    assert response.status_code == 404


def test_apply_validates_request_body(db, client):
    """
    【結合テスト項番8】異常系 - 入力チェックエラー（用途が空白のみ・201文字）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・有効な備品（A-1）が登録済み
    入力値：
    ・purpose が空白のみ
    ・purpose が201文字
    想定結果：2件とも HTTP 422
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, user, equipment, today, today, purpose="   ")
    assert response.status_code == 422
    response = _apply(client, user, equipment, today, today, purpose="あ" * 201)
    assert response.status_code == 422


@pytest.mark.parametrize("status", ["approved", "lent"])
def test_apply_rejects_overlap_with_approved_or_lent(db, client, status):
    """
    【結合テスト項番9】異常系 - 承認済み・貸出中の予約と期間が重複（パラメーター化2件）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザー（hanako）が登録済み。備品に、承認済み／貸出中のいずれかの予約（今日+2日〜+4日）
        がある
    入力値：今日+4日〜今日+6日で申請（予約の最終日と1日重なる）
    想定結果：
    ・HTTP 409
    ・detail が "指定した期間に承認済み・貸出中の予約があります"
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, other, status, today + timedelta(days=2), today + timedelta(days=4))
    response = _apply(client, user, equipment, today + timedelta(days=4), today + timedelta(days=6))
    assert response.status_code == 409
    assert response.json() == {"detail": "指定した期間に承認済み・貸出中の予約があります"}


def test_apply_allows_adjacent_period_and_requested_overlap(db, client):
    """
    【結合テスト項番10】境界値 - 予約の直後の期間・申請中の重複・返却済みの重複は申請できる
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザーが登録済み。備品に、承認済み（今日+2日〜+4日）・申請中（今日+10日〜+12日）
        ・返却済み（今日〜+1日）がある
    入力値：
    ・今日+5日〜+6日で申請（承認済みの直後）
    ・今日+11日〜+13日で申請（申請中と重複）
    ・今日〜今日+1日で申請（返却済みと重複）
    想定結果：3件とも HTTP 201
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, other, "approved", today + timedelta(days=2), today + timedelta(days=4))
    _make_loan(db, equipment, other, "requested", today + timedelta(days=10), today + timedelta(days=12))
    _make_loan(db, equipment, other, "returned", today, today + timedelta(days=1))
    assert _apply(client, user, equipment, today + timedelta(days=5), today + timedelta(days=6)).status_code == 201
    assert _apply(client, user, equipment, today + timedelta(days=11), today + timedelta(days=13)).status_code == 201
    assert _apply(client, user, equipment, today, today + timedelta(days=1)).status_code == 201


def test_apply_rejects_period_overlapping_overdue_lent_until_today(db, client):
    """
    【結合テスト項番11】境界値 - 期限超過の貸出中は今日まで占有している扱いで重複判定する
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザーが登録済み。備品が貸出中で返却予定日が2日前（期限超過）
    入力値：
    ・今日〜今日+1日で申請
    ・今日+1日〜+2日で申請
    想定結果：
    ・HTTP 409（今日まで占有中のため）
    ・HTTP 201（占有終了後）
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, other, "lent", today - timedelta(days=5), today - timedelta(days=2))
    response = _apply(client, user, equipment, today, today + timedelta(days=1))
    assert response.status_code == 409
    response = _apply(client, user, equipment, today + timedelta(days=1), today + timedelta(days=2))
    assert response.status_code == 201


# ---- 申請取消（本人） ----


@pytest.mark.parametrize("status", ["requested", "approved"])
def test_cancel_own_loan_success_without_notification(db, client, status):
    """
    【結合テスト項番12】正常系 - 自分の申請中・承認済みの申請を取り消せる。
      通知は作らない（パラメーター化2件）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者が登録済み。taro の申請（申請中／承認済み）がある
    入力値：POST /api/loan-requests/{ID}/cancel
    想定結果：
    ・HTTP 200
    ・status=canceled、reason が空文字、canceled_at が設定される
    ・取消者が本人
    ・通知は作られない
    """
    user = _make_user(db, "taro")
    _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, status, today + timedelta(days=1), today + timedelta(days=2))
    response = client.post(f"/api/loan-requests/{loan.id}/cancel", headers=_headers(user))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "canceled"
    assert body["reason"] == ""
    assert body["canceled_at"] is not None
    assert loan.canceled_by == user.id
    assert _notifications(db, loan.id) == []


@pytest.mark.parametrize("status", ["lent", "returned", "rejected", "canceled"])
def test_cancel_own_loan_rejects_other_status(db, client, status):
    """
    【結合テスト項番13】異常系 - 貸出中・返却済み・却下・取消済みの申請は取り消せない（パラメーター
      化4件）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・taro の申請（貸出中／返却済み／却下／取消済みのいずれか）がある
    入力値：POST /api/loan-requests/{ID}/cancel
    想定結果：
    ・HTTP 400
    ・detail が "申請中・承認済みの申請のみ取り消せます"
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, status, today - timedelta(days=1), today + timedelta(days=2))
    response = client.post(f"/api/loan-requests/{loan.id}/cancel", headers=_headers(user))
    assert response.status_code == 400
    assert response.json() == {"detail": "申請中・承認済みの申請のみ取り消せます"}


def test_cancel_own_loan_returns_404_for_other_users_or_missing(db, client):
    """
    【結合テスト項番14】セキュリティ - 他人の申請・存在しない申請は404（存在の有無を漏らさない）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザー（hanako）の申請中の申請がある
    入力値：
    ・taro が hanako の申請を取消
    ・taro が ID 999999 を取消
    想定結果：
    ・HTTP 404、detail が "申請が見つかりません"、hanako の申請は申請中のまま
    ・HTTP 404
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, other, "requested", today, today)
    response = client.post(f"/api/loan-requests/{loan.id}/cancel", headers=_headers(user))
    assert response.status_code == 404
    assert response.json() == {"detail": "申請が見つかりません"}
    assert loan.status == "requested"
    response = client.post("/api/loan-requests/999999/cancel", headers=_headers(user))
    assert response.status_code == 404


# ---- 申請承認 ----


def test_approve_success_notifies_requester(db, client):
    """
    【結合テスト項番23】正常系 - 申請を承認し、申請者へ「承認」通知を作る
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・申請中の申請（開始日=今日）がある
    入力値：POST /api/admin/loan-requests/{ID}/approve
    想定結果：
    ・HTTP 200
    ・status=approved、decided_at が設定される
    ・承認者が管理者
    ・通知は申請者宛の「承認」1件
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "requested", today, today + timedelta(days=2))
    response = client.post(f"/api/admin/loan-requests/{loan.id}/approve", headers=_headers(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["decided_at"] is not None
    assert loan.decided_by == admin.id
    notifications = _notifications(db, loan.id)
    assert [(n.recipient_id, n.type) for n in notifications] == [(user.id, NotificationType.APPROVED.value)]


def test_approve_rejects_not_found_and_non_requested_and_past_start(db, client):
    """
    【結合テスト項番24】異常系 - 存在しない申請は404。
      申請中でない申請・開始日を過ぎた申請は承認できない
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・有効な備品（A-1）が登録済み
    ・承認済みの申請と、開始日が昨日の申請中の申請がある
    入力値：
    ・ID 999999
    ・承認済みの申請
    ・開始日が昨日の申請中の申請
    想定結果：
    ・HTTP 404、detail が "申請が見つかりません"
    ・HTTP 400、detail が "申請中の申請のみ承認できます"
    ・HTTP 400、detail が "開始日を過ぎた申請は承認できません"
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = client.post("/api/admin/loan-requests/999999/approve", headers=_headers(admin))
    assert response.status_code == 404
    assert response.json() == {"detail": "申請が見つかりません"}
    approved = _make_loan(db, equipment, user, "approved", today, today)
    response = client.post(f"/api/admin/loan-requests/{approved.id}/approve", headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {"detail": "申請中の申請のみ承認できます"}
    stale = _make_loan(db, equipment, user, "requested", today - timedelta(days=1), today + timedelta(days=1))
    response = client.post(f"/api/admin/loan-requests/{stale.id}/approve", headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {"detail": "開始日を過ぎた申請は承認できません"}


def test_approve_second_overlapping_request_conflicts(db, client):
    """
    【結合テスト項番25】異常系 - 期間が重なる申請を2件目に承認しようとすると409
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー2人（taro・hanako）と管理者が登録済み。
        同じ備品に期間が重なる申請中の申請が2件ある（今日+1日〜+3日／今日+2日〜+4日）
    入力値：
    ・1件目を承認
    ・2件目を承認
    想定結果：
    ・HTTP 200
    ・HTTP 409、detail が "承認済み・貸出中の予約と期間が重複しているため承認できません"、
        2件目は申請中のまま
    """
    first_user = _make_user(db, "taro")
    second_user = _make_user(db, "hanako")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    first = _make_loan(db, equipment, first_user, "requested", today + timedelta(days=1), today + timedelta(days=3))
    second = _make_loan(db, equipment, second_user, "requested", today + timedelta(days=2), today + timedelta(days=4))
    assert client.post(f"/api/admin/loan-requests/{first.id}/approve", headers=_headers(admin)).status_code == 200
    response = client.post(f"/api/admin/loan-requests/{second.id}/approve", headers=_headers(admin))
    assert response.status_code == 409
    assert response.json() == {"detail": "承認済み・貸出中の予約と期間が重複しているため承認できません"}
    assert second.status == "requested"


def test_approve_ignores_itself_when_checking_overlap(db, client):
    """
    【結合テスト項番26】境界値 - 重複確認で自分自身の申請は数えない
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・申請中の申請（今日〜今日）がある
    入力値：
    ・承認前に重複件数を確認（自分を除外しない・する）
    ・承認
    ・承認後に重複件数を確認（自分を除外しない・する）
    想定結果：
    ・どちらも0件
    ・HTTP 200
    ・除外しない場合は1件、自分を除外する場合は0件
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "requested", today, today)
    assert crud.count_overlapping_reservations(db, equipment.id, today, today, today, None) == 0
    assert crud.count_overlapping_reservations(db, equipment.id, today, today, today, loan.id) == 0
    response = client.post(f"/api/admin/loan-requests/{loan.id}/approve", headers=_headers(admin))
    assert response.status_code == 200
    assert crud.count_overlapping_reservations(db, equipment.id, today, today, today, None) == 1
    assert crud.count_overlapping_reservations(db, equipment.id, today, today, today, loan.id) == 0


def test_approve_converts_exclusion_constraint_violation_to_409(db, client, monkeypatch):
    """
    【結合テスト項番27】異常系 - 承認の更新でDBの重複防止制約に違反した場合も409にする
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・申請中の申請がある。申請の更新処理が重複防止制約違反（IntegrityError）を返すよう差し替える
    入力値：POST /api/admin/loan-requests/{ID}/approve
    想定結果：
    ・HTTP 409
    ・detail が "承認済み・貸出中の予約と期間が重複しているため承認できません"
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "requested", today, today)

    def fake_decide(*args, **kwargs):
        raise IntegrityError("stmt", {}, Exception("ex_loan_request_equipment_period"))

    monkeypatch.setattr(crud, "decide_loan_request", fake_decide)
    response = client.post(f"/api/admin/loan-requests/{loan.id}/approve", headers=_headers(admin))
    assert response.status_code == 409
    assert response.json() == {"detail": "承認済み・貸出中の予約と期間が重複しているため承認できません"}


def test_approve_converts_commit_integrity_error_to_409(db, client, monkeypatch):
    """
    【結合テスト項番28】異常系 - 確定（コミット）時にDBの重複防止制約に違反した場合も409にする
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・申請中の申請がある。コミットが重複防止制約違反（IntegrityError）を返すよう差し替える
    入力値：POST /api/admin/loan-requests/{ID}/approve
    想定結果：HTTP 409
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "requested", today, today)

    def fake_commit():
        raise IntegrityError("stmt", {}, Exception("ex_loan_request_equipment_period"))

    monkeypatch.setattr(db, "commit", fake_commit)
    response = client.post(f"/api/admin/loan-requests/{loan.id}/approve", headers=_headers(admin))
    assert response.status_code == 409


def test_database_exclusion_constraint_blocks_double_approval(db):
    """
    【結合テスト項番29】DB制約 - 同じ備品の承認済みの期間が重なる登録をDBが拒否する
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・同じ備品に承認済みの申請（今日〜+2日）が登録済み
    入力値：期間が重なる承認済みの申請（今日+1日〜+3日）を直接登録
    想定結果：IntegrityError（DBの重複防止制約）が発生する
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, user, "approved", today, today + timedelta(days=2))
    with pytest.raises(IntegrityError):
        _make_loan(db, equipment, user, "approved", today + timedelta(days=1), today + timedelta(days=3))
    db.rollback()


# ---- 申請却下 ----


def test_reject_success_notifies_requester_with_reason(db, client):
    """
    【結合テスト項番30】正常系 - 申請を却下し、理由を保存して申請者へ「却下」通知を作る
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・申請中の申請がある
    入力値：{"reason": "  在庫なし  "}
    想定結果：
    ・HTTP 200
    ・status=rejected、reason=在庫なし（前後の空白を除去）
    ・通知は申請者宛の「却下」1件
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "requested", today, today)
    response = client.post(
        f"/api/admin/loan-requests/{loan.id}/reject", headers=_headers(admin), json={"reason": "  在庫なし  "}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "在庫なし"
    notifications = _notifications(db, loan.id)
    assert [(n.recipient_id, n.type) for n in notifications] == [(user.id, NotificationType.REJECTED.value)]


def test_reject_errors(db, client):
    """
    【結合テスト項番31】異常系 - 存在しない申請は404、申請中でない申請は400、理由が不正・欠落は422
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・承認済みの申請と、申請中の申請がある
    入力値：
    ・ID 999999
    ・承認済みの申請
    ・reason が空白のみ／201文字／欠落
    想定結果：
    ・HTTP 404
    ・HTTP 400、detail が "申請中の申請のみ却下できます"
    ・3件とも HTTP 422
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    payload = {"reason": "理由"}
    response = client.post("/api/admin/loan-requests/999999/reject", headers=_headers(admin), json=payload)
    assert response.status_code == 404
    approved = _make_loan(db, equipment, user, "approved", today, today)
    response = client.post(f"/api/admin/loan-requests/{approved.id}/reject", headers=_headers(admin), json=payload)
    assert response.status_code == 400
    assert response.json() == {"detail": "申請中の申請のみ却下できます"}
    requested = _make_loan(db, equipment, user, "requested", today + timedelta(days=5), today + timedelta(days=6))
    for bad in ({"reason": "   "}, {"reason": "あ" * 201}, {}):
        response = client.post(f"/api/admin/loan-requests/{requested.id}/reject", headers=_headers(admin), json=bad)
        assert response.status_code == 422


# ---- 申請管理者取消 ----


def test_admin_cancel_success_notifies_requester(db, client):
    """
    【結合テスト項番32】正常系 - 承認済みの申請を管理者が取り消し、申請者へ「取消」通知を作る
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・承認済みの申請がある
    入力値：{"reason": "故障のため"}
    想定結果：
    ・HTTP 200
    ・status=canceled、reason=故障のため
    ・取消者が管理者
    ・通知は申請者宛の「取消」1件
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today + timedelta(days=1), today + timedelta(days=2))
    response = client.post(
        f"/api/admin/loan-requests/{loan.id}/admin-cancel", headers=_headers(admin), json={"reason": "故障のため"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "canceled"
    assert body["reason"] == "故障のため"
    assert loan.canceled_by == admin.id
    notifications = _notifications(db, loan.id)
    assert [(n.recipient_id, n.type) for n in notifications] == [(user.id, NotificationType.CANCELED.value)]


@pytest.mark.parametrize("status", ["requested", "lent", "returned", "rejected", "canceled"])
def test_admin_cancel_rejects_non_approved(db, client, status):
    """
    【結合テスト項番33】異常系 - 承認済み以外の申請は管理者取消できない（パラメーター化5件）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・申請（申請中／貸出中／返却済み／却下／取消済みのいずれか）がある
    入力値：{"reason": "理由"}
    想定結果：
    ・HTTP 400
    ・detail が "承認済みの申請のみ管理者取消できます"
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, status, today - timedelta(days=1), today + timedelta(days=1))
    response = client.post(
        f"/api/admin/loan-requests/{loan.id}/admin-cancel", headers=_headers(admin), json={"reason": "理由"}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "承認済みの申請のみ管理者取消できます"}


def test_admin_cancel_not_found_and_blank_reason(db, client):
    """
    【結合テスト項番34】異常系 - 存在しない申請は404、理由が空白のみは422
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin1）が登録済み・トークン取得済み
    入力値：
    ・ID 999999（理由あり）
    ・reason が空白のみ
    想定結果：
    ・HTTP 404
    ・HTTP 422
    """
    admin = _make_user(db, "admin1", role="admin")
    response = client.post(
        "/api/admin/loan-requests/999999/admin-cancel", headers=_headers(admin), json={"reason": "理由"}
    )
    assert response.status_code == 404
    response = client.post("/api/admin/loan-requests/1/admin-cancel", headers=_headers(admin), json={"reason": " "})
    assert response.status_code == 422


# ---- 一覧・取得 ----


def test_admin_list_defaults_to_requested_oldest_first(db, client):
    """
    【結合テスト項番21】正常系 - 既定は申請中だけを古い順に返し、状態で絞り込める。不正な状態は422
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・申請中2件（申請日時が異なる）と承認済み1件がある
    入力値：
    ・クエリなし
    ・status=approved
    ・status=unknown
    想定結果：
    ・HTTP 200、total=2、申請日時の古い順（承認済みは含まれない）
    ・total=1
    ・HTTP 422
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    now = get_now()
    newer = _make_loan(db, equipment, user, "requested", today + timedelta(days=10), today + timedelta(days=11), now)
    older = _make_loan(
        db,
        equipment,
        user,
        "requested",
        today + timedelta(days=20),
        today + timedelta(days=21),
        now - timedelta(hours=1),
    )
    _make_loan(db, equipment, user, "approved", today + timedelta(days=30), today + timedelta(days=31))
    response = client.get("/api/admin/loan-requests", headers=_headers(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [older.id, newer.id]
    response = client.get("/api/admin/loan-requests?status=approved", headers=_headers(admin))
    assert response.json()["total"] == 1
    response = client.get("/api/admin/loan-requests?status=unknown", headers=_headers(admin))
    assert response.status_code == 422


def test_admin_list_shows_overdue_fields(db, client):
    """
    【結合テスト項番22】正常系 - 貸出中で期限超過の申請は is_overdue=true と遅延日数を返す
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・貸出中の申請があり、返却予定日が3日前
    入力値：status=lent
    想定結果：
    ・is_overdue=true
    ・overdue_days=3
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, user, "lent", today - timedelta(days=5), today - timedelta(days=3))
    response = client.get("/api/admin/loan-requests?status=lent", headers=_headers(admin))
    item = response.json()["items"][0]
    assert item["is_overdue"] is True
    assert item["overdue_days"] == 3


def test_my_list_shows_only_own_newest_first_with_optional_status(db, client):
    """
    【結合テスト項番16】正常系 - 自分の申請だけを新しい順に返し、状態で絞り込める
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザー（hanako）の申請中の申請がある（絞り込み対象外のデータが存在する）。
        taro の申請が返却済み（古い）・申請中（新しい）の2件ある
    入力値：
    ・クエリなし
    ・status=returned
    想定結果：
    ・HTTP 200、total=2、申請日時の新しい順（申請中→返却済み）で、hanako の申請は含まれない
    ・返却済みの1件だけ
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    now = get_now()
    old = _make_loan(
        db, equipment, user, "returned", today - timedelta(days=9), today - timedelta(days=8), now - timedelta(days=9)
    )
    new = _make_loan(db, equipment, user, "requested", today + timedelta(days=5), today + timedelta(days=6), now)
    _make_loan(db, equipment, other, "requested", today + timedelta(days=7), today + timedelta(days=8), now)
    response = client.get("/api/loan-requests/me", headers=_headers(user))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [new.id, old.id]
    response = client.get("/api/loan-requests/me?status=returned", headers=_headers(user))
    assert [item["id"] for item in response.json()["items"]] == [old.id]


def test_my_list_paging(db, client):
    """
    【結合テスト項番17】正常系 - ページングができる
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・taro の申請が3件ある
    入力値：page=2&page_size=2
    想定結果：
    ・total=3・page=2
    ・items が1件
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    now = get_now()
    for index in range(3):
        start = today + timedelta(days=index * 3)
        _make_loan(db, equipment, user, "requested", start, start + timedelta(days=1), now + timedelta(minutes=index))
    response = client.get("/api/loan-requests/me?page=2&page_size=2", headers=_headers(user))
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert len(body["items"]) == 1


def test_get_my_loan_request_and_other_users_returns_404(db, client):
    """
    【結合テスト項番18】正常系・異常系 - 自分の申請を取得できる。他人の申請・存在しない申請は404、
      ID形式不正は422
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザー（hanako）の申請がある
    入力値：
    ・自分の申請を取得
    ・hanako の申請を取得
    ・ID 999999
    ・ID 0
    想定結果：
    ・HTTP 200、id が一致
    ・HTTP 404、detail が "申請が見つかりません"
    ・HTTP 404
    ・HTTP 422
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    mine = _make_loan(db, equipment, user, "requested", today, today)
    theirs = _make_loan(db, equipment, other, "requested", today + timedelta(days=3), today + timedelta(days=4))
    response = client.get(f"/api/loan-requests/me/{mine.id}", headers=_headers(user))
    assert response.status_code == 200
    assert response.json()["id"] == mine.id
    response = client.get(f"/api/loan-requests/me/{theirs.id}", headers=_headers(user))
    assert response.status_code == 404
    assert response.json() == {"detail": "申請が見つかりません"}
    response = client.get("/api/loan-requests/me/999999", headers=_headers(user))
    assert response.status_code == 404
    response = client.get("/api/loan-requests/me/0", headers=_headers(user))
    assert response.status_code == 422


def test_history_keeps_names_of_deactivated_equipment_and_user(db, client):
    """
    【結合テスト項番19】正常系 - 備品が無効化されても、履歴に備品名・申請者名が残る
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（太郎）が登録済み。返却済みの申請があり、その備品を無効化済み
    入力値：太郎が自分の申請を取得
    想定結果：
    ・HTTP 200
    ・equipment_name=ノートPC、requester_name=太郎、status=returned
    """
    user = _make_user(db, "taro", name="太郎")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "returned", today - timedelta(days=3), today - timedelta(days=2))
    equipment.is_active = False
    db.flush()
    response = client.get(f"/api/loan-requests/me/{loan.id}", headers=_headers(user))
    assert response.status_code == 200
    body = response.json()
    assert body["equipment_name"] == "ノートPC"
    assert body["requester_name"] == "太郎"
    assert body["status"] == "returned"
