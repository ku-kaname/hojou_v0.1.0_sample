"""
結合テスト：通知・日次処理（通知一覧・サマリー・既読化・全件既読化・日次処理・スケジューラー）

設計書：設計書/サーバー処理（main）/貸出・返却・履歴/、設計書/CRUD/貸出・返却・履歴/、設計書/日次処理（scheduler）/
"""

import itertools
import logging
from contextlib import nullcontext
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import auth, crud, main, scheduler, services
from app.database import get_db
from app.main import app
from app.models import Equipment, LoanRequest, Notification, User
from app.services import get_now, get_today

PASSWORD = "Password123"

_auto_numbers = itertools.count(1)
_STEP_NAMES = ["no_show_cancel", "unapproved_cancel", "due_soon", "overdue"]


@pytest.fixture()
def client(db):
    """テスト用DBセッションを使うAPIクライアント"""
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_user(db, login_id, role="general", name="山田", department="営業部") -> User:
    user = User(
        login_id=login_id,
        name=name,
        department=department,
        password_hash=auth.hash_password(PASSWORD),
        role=role,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    return user


def _headers(user) -> dict[str, str]:
    token = auth.create_access_token(user.id, user.token_generation, get_now())
    return {"Authorization": f"Bearer {token}"}


def _make_equipment(db, asset_number, name="ノートPC", is_active=True) -> Equipment:
    equipment = Equipment(asset_number=asset_number, name=name, category="PC", is_active=is_active)
    db.add(equipment)
    db.flush()
    return equipment


def _make_loan(db, equipment, requester, status, start, due) -> LoanRequest:
    if equipment is None:
        equipment = _make_equipment(db, f"AUTO-{next(_auto_numbers)}")
    loan = LoanRequest(
        equipment_id=equipment.id,
        requester_id=requester.id,
        start_date=start,
        due_date=due,
        purpose="test",
        status=status,
        requested_at=get_now(),
    )
    if status == "lent":
        loan.lent_at = get_now()
    db.add(loan)
    db.flush()
    return loan


def _make_notification(db, recipient, loan, notification_type="approved", is_read=False, day=None) -> Notification:
    notification = Notification(
        recipient_id=recipient.id,
        type=notification_type,
        loan_request_id=loan.id,
        is_read=is_read,
        notified_date=day or get_today(),
    )
    db.add(notification)
    db.flush()
    return notification


def _count_notifications(db, notification_type=None) -> int:
    statement = select(func.count()).select_from(Notification)
    if notification_type is not None:
        statement = statement.where(Notification.type == notification_type)
    return db.scalar(statement)


# ---- 認証 ----


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/notifications"),
        ("get", "/api/notifications/summary"),
        ("post", "/api/notifications/1/read"),
        ("post", "/api/notifications/read-all"),
    ],
)
def test_endpoints_require_authentication(client, method, path):
    """
    【結合テスト項番1】異常系 - 認証なしは401（通知の全4エンドポイント共通）
    前提条件：
    ・テスト用DB起動済み
    入力値：トークンなしで GET /api/notifications・GET /api/notifications/summary・POST /api/notific
      ations/1/read・POST /api/notifications/read-all
    想定結果：4件とも HTTP 401
    """
    assert getattr(client, method)(path).status_code == 401


# ---- 通知一覧 ----


def test_list_returns_only_own_notifications_newest_first(db, client):
    """
    【結合テスト項番2】正常系 - 自分宛の通知だけを新しい順に返す
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザー（hanako）が登録済み。備品（ノートPC）と承認済みの申請があり、
        taro 宛の通知2件（承認・取消）と hanako 宛の通知1件がある
    入力値：クエリなし
    想定結果：
    ・HTTP 200、total=2
    ・新しい順（取消→承認）で、hanako 宛は含まれない
    ・項目に種類・申請ID・資産番号・備品名・既読状態（false）・通知日・作成日時（+09:00）が入る
    ・宛先ID（recipient_id）は返さない
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1", name="ノートPC")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today + timedelta(days=3))
    first = _make_notification(db, user, loan, "approved")
    second = _make_notification(db, user, loan, "canceled")
    _make_notification(db, other, loan, "approved")

    response = client.get("/api/notifications", headers=_headers(user))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [second.id, first.id]
    item = body["items"][1]
    assert item["type"] == "approved"
    assert item["loan_request_id"] == loan.id
    assert item["equipment_asset_number"] == "A-1"
    assert item["equipment_name"] == "ノートPC"
    assert item["is_read"] is False
    assert item["notified_date"] == today.isoformat()
    assert item["created_at"].endswith("+09:00")
    assert "recipient_id" not in item


def test_list_includes_notifications_of_inactive_equipment(db, client):
    """
    【結合テスト項番3】正常系 - 備品が無効化されていても通知は一覧に出る
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・無効化済みの備品と、その申請・通知が登録済み
    入力値：クエリなし
    想定結果：total=1
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1", is_active=False)
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)
    _make_notification(db, user, loan)
    response = client.get("/api/notifications", headers=_headers(user))
    assert response.json()["total"] == 1


def test_list_filters_by_is_read(db, client):
    """
    【結合テスト項番4】正常系 - 既読状態（is_read）で絞り込める
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品と、taro の承認済みの申請が登録済み。未読の通知1件と既読の通知1件がある
    入力値：
    ・is_read=false
    ・is_read=true
    ・指定なし
    想定結果：
    ・未読の1件だけ
    ・既読の1件だけ
    ・total=2
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)
    unread = _make_notification(db, user, loan, "approved", is_read=False)
    read = _make_notification(db, user, loan, "rejected", is_read=True)

    unread_body = client.get("/api/notifications?is_read=false", headers=_headers(user)).json()
    assert [item["id"] for item in unread_body["items"]] == [unread.id]
    read_body = client.get("/api/notifications?is_read=true", headers=_headers(user)).json()
    assert [item["id"] for item in read_body["items"]] == [read.id]
    all_body = client.get("/api/notifications", headers=_headers(user)).json()
    assert all_body["total"] == 2


def test_list_paginates(db, client):
    """
    【結合テスト項番5】正常系 - ページングができる
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品と、taro の承認済みの申請が登録済み。通知が3件ある
    入力値：page=2&page_size=2
    想定結果：
    ・total=3・page=2・page_size=2
    ・items が最も古い1件
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)
    created = [_make_notification(db, user, loan) for _ in range(3)]

    body = client.get("/api/notifications?page=2&page_size=2", headers=_headers(user)).json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert [item["id"] for item in body["items"]] == [created[0].id]


def test_list_rejects_invalid_paging(db, client):
    """
    【結合テスト項番6】異常系 - ページ番号0・1ページの件数101は422
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    入力値：
    ・page=0
    ・page_size=101
    想定結果：2件とも HTTP 422
    """
    user = _make_user(db, "taro")
    assert client.get("/api/notifications?page=0", headers=_headers(user)).status_code == 422
    assert client.get("/api/notifications?page_size=101", headers=_headers(user)).status_code == 422


# ---- 通知サマリー ----


def test_summary_for_general_user_has_no_pending_count(db, client):
    """
    【結合テスト項番7】正常系 - 一般ユーザーは自分の未読件数だけを返し、承認待ち件数は null
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザー（hanako）が登録済み。申請中の申請と、taro 宛の未読1件・既読1件、
        hanako 宛の未読1件がある
    入力値：GET /api/notifications/summary
    想定結果：{"unread_count": 1, "pending_request_count": null}
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "requested", today, today)
    _make_notification(db, user, loan, is_read=False)
    _make_notification(db, user, loan, is_read=True)
    _make_notification(db, other, loan, is_read=False)

    body = client.get("/api/notifications/summary", headers=_headers(user)).json()
    assert body == {"unread_count": 1, "pending_request_count": None}


def test_summary_for_admin_includes_pending_count(db, client):
    """
    【結合テスト項番8】正常系 - 管理者は承認待ち（申請中）の件数も返す
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin1）が登録済み・トークン取得済み
    ・一般ユーザー（taro）が登録済み。申請中2件・承認済み1件の申請がある
    入力値：管理者で GET /api/notifications/summary
    想定結果：{"unread_count": 0, "pending_request_count": 2}
    """
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, user, "requested", today, today)
    _make_loan(db, equipment, user, "requested", today + timedelta(days=5), today + timedelta(days=6))
    _make_loan(db, equipment, user, "approved", today + timedelta(days=10), today + timedelta(days=11))

    body = client.get("/api/notifications/summary", headers=_headers(admin)).json()
    assert body == {"unread_count": 0, "pending_request_count": 2}


# ---- 通知既読化 ----


def test_mark_read_updates_notification(db, client):
    """
    【結合テスト項番9】正常系 - 自分宛の未読通知を既読にする
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品と、taro の承認済みの申請が登録済み。taro 宛の未読の通知がある
    入力値：POST /api/notifications/{ID}/read
    想定結果：
    ・HTTP 204、本文なし
    ・DBの通知が既読になる
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)
    notification = _make_notification(db, user, loan)

    response = client.post(f"/api/notifications/{notification.id}/read", headers=_headers(user))
    assert response.status_code == 204
    assert response.content == b""
    db.refresh(notification)
    assert notification.is_read is True


def test_mark_read_is_idempotent(db, client):
    """
    【結合テスト項番10】境界値 - すでに既読の通知を既読にしてもエラーにならない（冪等）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品と、taro の承認済みの申請が登録済み。taro 宛の既読の通知がある
    入力値：POST /api/notifications/{ID}/read
    想定結果：HTTP 204
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)
    notification = _make_notification(db, user, loan, is_read=True)

    response = client.post(f"/api/notifications/{notification.id}/read", headers=_headers(user))
    assert response.status_code == 204


def test_mark_read_of_other_users_notification_is_not_found(db, client):
    """
    【結合テスト項番11】セキュリティ - 他人宛の通知は404（存在を漏らさず、既読にもしない）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザー（hanako）が登録済み。備品と、taro の承認済みの申請が登録済み。
        hanako 宛の未読の通知がある
    入力値：taro が hanako 宛の通知IDで POST /api/notifications/{ID}/read
    想定結果：
    ・HTTP 404
    ・detail が "通知が見つかりません"
    ・hanako 宛の通知は未読のまま
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)
    notification = _make_notification(db, other, loan)

    response = client.post(f"/api/notifications/{notification.id}/read", headers=_headers(user))
    assert response.status_code == 404
    assert response.json()["detail"] == "通知が見つかりません"
    db.refresh(notification)
    assert notification.is_read is False


def test_mark_read_of_missing_notification_is_not_found(db, client):
    """
    【結合テスト項番12】異常系 - 存在しない通知は404
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    入力値：ID 999999
    想定結果：
    ・HTTP 404
    ・detail が "通知が見つかりません"
    """
    user = _make_user(db, "taro")
    response = client.post("/api/notifications/999999/read", headers=_headers(user))
    assert response.status_code == 404
    assert response.json()["detail"] == "通知が見つかりません"


def test_mark_read_rejects_invalid_id(db, client):
    """
    【結合テスト項番13】異常系 - ID形式不正（0）は422
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    入力値：ID 0
    想定結果：HTTP 422
    """
    user = _make_user(db, "taro")
    assert client.post("/api/notifications/0/read", headers=_headers(user)).status_code == 422


# ---- 通知全件既読化 ----


def test_mark_all_read_updates_only_own_unread(db, client):
    """
    【結合テスト項番14】正常系 - 自分宛の未読通知だけを全件既読にし、件数を返す
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・他のユーザー（hanako）が登録済み。備品と、taro の承認済みの申請が登録済み。
        taro 宛の未読2件・既読1件、hanako 宛の未読1件がある
    入力値：POST /api/notifications/read-all
    想定結果：
    ・HTTP 200、updated_count=2
    ・taro の未読件数が0
    ・hanako 宛の通知は未読のまま
    """
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)
    _make_notification(db, user, loan, is_read=False)
    _make_notification(db, user, loan, is_read=False)
    _make_notification(db, user, loan, is_read=True)
    others = _make_notification(db, other, loan, is_read=False)

    response = client.post("/api/notifications/read-all", headers=_headers(user))
    assert response.status_code == 200
    assert response.json() == {"updated_count": 2}
    assert crud.count_unread_notifications(db, user.id) == 0
    db.refresh(others)
    assert others.is_read is False


def test_mark_all_read_with_nothing_unread_returns_zero(db, client):
    """
    【結合テスト項番15】境界値 - 未読が0件なら updated_count=0
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    入力値：POST /api/notifications/read-all
    想定結果：{"updated_count": 0}
    """
    user = _make_user(db, "taro")
    response = client.post("/api/notifications/read-all", headers=_headers(user))
    assert response.json() == {"updated_count": 0}


# ---- 日次処理（サービス層） ----


def test_daily_job_cancels_no_show_approved_after_due_date(db):
    """
    【結合テスト項番16】正常系 - 貸出処理されないまま返却予定日を過ぎた承認済みの申請を自動取消し、
      申請者へ通知する
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・承認済みの申請2件（返却予定日=昨日／今日）がある
    入力値：日次処理を実行（services.run_daily_job）
    想定結果：
    ・no_show_cancel=1、failed_steps が空
    ・返却予定日=昨日の申請が取消済みになり、取消者は null、
        理由が「貸出処理されないまま返却予定日を過ぎたため、自動的に取消しました」
    ・返却予定日=今日の申請は承認済みのまま
    ・通知は申請者宛の「取消」1件（通知日=今日）
    """
    user = _make_user(db, "taro")
    today = get_today()
    overdue_approved = _make_loan(db, None, user, "approved", today - timedelta(days=3), today - timedelta(days=1))
    still_valid = _make_loan(db, None, user, "approved", today - timedelta(days=1), today)

    result = services.run_daily_job(db)

    assert result["no_show_cancel"] == 1
    assert result["failed_steps"] == []
    db.refresh(overdue_approved)
    assert overdue_approved.status == "canceled"
    assert overdue_approved.canceled_by is None
    assert overdue_approved.canceled_at is not None
    assert overdue_approved.reason == "貸出処理されないまま返却予定日を過ぎたため、自動的に取消しました"
    db.refresh(still_valid)
    assert still_valid.status == "approved"
    notifications = db.scalars(select(Notification).where(Notification.type == "canceled")).all()
    assert [(n.recipient_id, n.loan_request_id, n.notified_date) for n in notifications] == [
        (user.id, overdue_approved.id, today)
    ]


def test_daily_job_cancels_unapproved_requests_after_start_date(db):
    """
    【結合テスト項番17】正常系 - 承認・却下されないまま開始日を過ぎた申請中の申請を自動取消し、
      申請者へ通知する
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・申請中の申請2件（開始日=昨日／今日）がある
    入力値：日次処理を実行（services.run_daily_job）
    想定結果：
    ・unapproved_cancel=1
    ・開始日=昨日の申請が取消済みになり、取消者は null、
        理由が「承認・却下されないまま開始日を過ぎたため、自動的に取消しました」
    ・開始日=今日の申請は申請中のまま
    ・「取消」通知が1件
    """
    user = _make_user(db, "taro")
    today = get_today()
    expired = _make_loan(db, None, user, "requested", today - timedelta(days=1), today + timedelta(days=2))
    valid = _make_loan(db, None, user, "requested", today, today + timedelta(days=2))

    result = services.run_daily_job(db)

    assert result["unapproved_cancel"] == 1
    db.refresh(expired)
    assert expired.status == "canceled"
    assert expired.canceled_by is None
    assert expired.reason == "承認・却下されないまま開始日を過ぎたため、自動的に取消しました"
    db.refresh(valid)
    assert valid.status == "requested"
    assert _count_notifications(db, "canceled") == 1


def test_daily_job_does_not_touch_other_statuses(db):
    """
    【結合テスト項番18】正常系 - 返却済み・却下・取消済みの申請は変更せず、通知も作らない
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・過去の日付の返却済み・却下・取消済みの申請が1件ずつある
    入力値：日次処理を実行（services.run_daily_job）
    想定結果：
    ・no_show_cancel=0、unapproved_cancel=0
    ・3件とも状態が変わらない
    ・通知は0件
    """
    user = _make_user(db, "taro")
    today = get_today()
    past = today - timedelta(days=5)
    loans = [
        _make_loan(db, None, user, status, past, past + timedelta(days=1))
        for status in ("returned", "rejected", "canceled")
    ]

    result = services.run_daily_job(db)

    assert result["no_show_cancel"] == 0
    assert result["unapproved_cancel"] == 0
    for loan, status in zip(loans, ("returned", "rejected", "canceled"), strict=True):
        db.refresh(loan)
        assert loan.status == status
    assert _count_notifications(db) == 0


def test_daily_job_notifies_lent_loans_due_tomorrow(db):
    """
    【結合テスト項番19】正常系 - 返却予定日が明日の貸出中の申請について、
      申請者へ「返却期限前日」通知を作る
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・貸出中の申請3件（返却予定日=明日／明後日／今日）がある
    入力値：日次処理を実行（services.run_daily_job）
    想定結果：
    ・due_soon=1
    ・通知は返却予定日=明日の申請についての1件（宛先は申請者、通知日=今日）
    """
    user = _make_user(db, "taro")
    today = get_today()
    due_tomorrow = _make_loan(db, None, user, "lent", today - timedelta(days=2), today + timedelta(days=1))
    _make_loan(db, None, user, "lent", today - timedelta(days=2), today + timedelta(days=2))
    _make_loan(db, None, user, "lent", today - timedelta(days=2), today)

    result = services.run_daily_job(db)

    assert result["due_soon"] == 1
    notifications = db.scalars(select(Notification).where(Notification.type == "due_soon")).all()
    assert [(n.recipient_id, n.loan_request_id, n.notified_date) for n in notifications] == [
        (user.id, due_tomorrow.id, today)
    ]


def test_daily_job_notifies_overdue_lent_loans(db):
    """
    【結合テスト項番20】正常系 - 返却予定日を過ぎた貸出中の申請について、
      申請者へ「期限超過」通知を作る（申請の状態は変えない）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・貸出中の申請2件（返却予定日=昨日／今日）がある
    入力値：日次処理を実行（services.run_daily_job）
    想定結果：
    ・overdue=1
    ・通知は返却予定日=昨日の申請についての1件
    ・申請は貸出中のまま
    """
    user = _make_user(db, "taro")
    today = get_today()
    overdue = _make_loan(db, None, user, "lent", today - timedelta(days=5), today - timedelta(days=1))
    _make_loan(db, None, user, "lent", today - timedelta(days=5), today)

    result = services.run_daily_job(db)

    assert result["overdue"] == 1
    notifications = db.scalars(select(Notification).where(Notification.type == "overdue")).all()
    assert [(n.recipient_id, n.loan_request_id, n.notified_date) for n in notifications] == [
        (user.id, overdue.id, today)
    ]
    db.refresh(overdue)
    assert overdue.status == "lent"


def test_daily_job_is_idempotent_on_same_day(db):
    """
    【結合テスト項番21】境界値 - 同じ日に2回実行しても、通知が重複しない
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・承認済み（期限超過）・申請中（開始日超過）・貸出中（返却予定日=明日）・貸出中（期限超過）
        の申請が1件ずつある
    入力値：
    ・日次処理を実行
    ・続けて日次処理をもう一度実行
    想定結果：
    ・1回目：各段階の件数が1、failed_steps が空、通知が4件
    ・2回目：取消の件数が0、failed_steps が空、通知は4件のまま
    """
    user = _make_user(db, "taro")
    today = get_today()
    _make_loan(db, None, user, "approved", today - timedelta(days=3), today - timedelta(days=1))
    _make_loan(db, None, user, "requested", today - timedelta(days=1), today + timedelta(days=2))
    _make_loan(db, None, user, "lent", today - timedelta(days=2), today + timedelta(days=1))
    _make_loan(db, None, user, "lent", today - timedelta(days=5), today - timedelta(days=1))

    first = services.run_daily_job(db)
    count_after_first = _count_notifications(db)
    second = services.run_daily_job(db)

    assert first == {"no_show_cancel": 1, "unapproved_cancel": 1, "due_soon": 1, "overdue": 1, "failed_steps": []}
    assert count_after_first == 4
    assert second["no_show_cancel"] == 0
    assert second["unapproved_cancel"] == 0
    assert second["failed_steps"] == []
    assert _count_notifications(db) == count_after_first


def test_daily_job_result_has_all_step_keys(db):
    """
    【結合テスト項番22】境界値 - 対象がなくても結果に全段階のキーが含まれる
    前提条件：
    ・テスト用DB起動済み
    ・申請が登録されていない
    入力値：日次処理を実行（services.run_daily_job）
    想定結果：{"no_show_cancel": 0, "unapproved_cancel": 0, "due_soon": 0, "overdue": 0, "failed_ste
      ps": []}
    """
    result = services.run_daily_job(db)
    assert result == {"no_show_cancel": 0, "unapproved_cancel": 0, "due_soon": 0, "overdue": 0, "failed_steps": []}


def test_daily_job_continues_after_failed_step(db, monkeypatch, caplog):
    """
    【結合テスト項番23】異常系 - 途中の段階が失敗しても後続の段階を続け、ログに例外の詳細を出さない
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・承認済み（期限超過）・貸出中（期限超過）の申請が1件ずつある。
        自動取消の更新処理が例外「secret-detail」を出すよう差し替える
    入力値：日次処理を実行（services.run_daily_job）
    想定結果：
    ・failed_steps が [no_show_cancel]、no_show_cancel=0
    ・overdue=1（後続の段階は実行される）
    ・承認済みの申請は変更されず、「取消」通知は0件、「期限超過」通知は1件
    ・ログに段階名と例外の種類（RuntimeError）は出るが、「secret-detail」は出ない
    """
    user = _make_user(db, "taro")
    today = get_today()
    stuck = _make_loan(db, None, user, "approved", today - timedelta(days=3), today - timedelta(days=1))
    overdue = _make_loan(db, None, user, "lent", today - timedelta(days=5), today - timedelta(days=1))
    db.commit()

    def _fail(*_args, **_kwargs):
        raise RuntimeError("secret-detail")

    monkeypatch.setattr(crud, "auto_cancel_loan_request", _fail)
    with caplog.at_level(logging.ERROR, logger="app"):
        result = services.run_daily_job(db)

    assert result["failed_steps"] == ["no_show_cancel"]
    assert result["no_show_cancel"] == 0
    assert result["overdue"] == 1
    db.refresh(stuck)
    assert stuck.status == "approved"
    assert _count_notifications(db, "canceled") == 0
    assert _count_notifications(db, "overdue") == 1
    db.refresh(overdue)
    assert overdue.status == "lent"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "no_show_cancel" in messages
    assert "RuntimeError" in messages
    assert "secret-detail" not in messages


def test_daily_job_reports_every_failed_step(db, monkeypatch):
    """
    【結合テスト項番24】異常系 - 全段階が失敗したら、すべての段階名を failed_steps に入れる
    前提条件：
    ・テスト用DB起動済み
    ・自動取消対象の取得・返却期限前日の取得・期限超過の取得が、それぞれ例外を出すよう差し替える
    入力値：日次処理を実行（services.run_daily_job）
    想定結果：
    ・failed_steps に全段階名が入る
    ・各段階の件数が0
    """

    def _fail(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(crud, "get_auto_cancel_targets_for_update", _fail)
    monkeypatch.setattr(crud, "get_lent_loan_requests_due_on", _fail)
    monkeypatch.setattr(crud, "get_overdue_lent_loan_requests", _fail)

    result = services.run_daily_job(db)

    assert result["failed_steps"] == _STEP_NAMES
    for step_name in _STEP_NAMES:
        assert result[step_name] == 0


# ---- CRUD ----


def test_get_auto_cancel_targets_ignores_unsupported_status(db):
    """
    【結合テスト項番25】異常系 - 対象外の状態（貸出中）を指定すると空を返す
    前提条件：
    ・テスト用DB起動済み
    入力値：状態=lent、基準日=今日
    想定結果：空のリスト
    """
    assert crud.get_auto_cancel_targets_for_update(db, "lent", get_today()) == []


def test_get_auto_cancel_targets_are_ordered_by_id(db):
    """
    【結合テスト項番26】正常系 - 対象の申請をID順（ロックの取得順を一定にする）で返す
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・有効な備品（A-1）が登録済み
    ・承認済みの申請2件（返却予定日=今日-8日／今日-4日）がある
    入力値：状態=approved、基準日=今日
    想定結果：先に登録した申請、後に登録した申請の順で返る
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    first = _make_loan(db, equipment, user, "approved", today - timedelta(days=9), today - timedelta(days=8))
    second = _make_loan(db, equipment, user, "approved", today - timedelta(days=5), today - timedelta(days=4))
    targets = crud.get_auto_cancel_targets_for_update(db, "approved", today)
    assert [loan.id for loan in targets] == [first.id, second.id]


# ---- スケジューラー ----


def test_scheduler_run_daily_job_calls_service_and_closes_session(db, monkeypatch, caplog):
    """
    【結合テスト項番27】正常系 - 日次処理を呼び出して完了ログ（INFO）を1件出し、DB接続を閉じる
    前提条件：
    ・テスト用DB起動済み
    ・サービス層の日次処理を、失敗なしの結果を返すよう差し替える
    入力値：scheduler.run_daily_job()
    想定結果：
    ・「日次処理が完了しました」のログが1件（INFOレベル）
    ・DB接続が閉じられる
    """
    session = _FakeSession()
    monkeypatch.setattr(scheduler, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        services,
        "run_daily_job",
        lambda _db: {"no_show_cancel": 1, "unapproved_cancel": 0, "due_soon": 2, "overdue": 3, "failed_steps": []},
    )
    with caplog.at_level(logging.INFO, logger="app"):
        scheduler.run_daily_job()

    assert session.closed is True
    records = [record for record in caplog.records if "日次処理が完了しました" in record.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO


def test_scheduler_run_daily_job_logs_error_when_step_failed(monkeypatch, caplog):
    """
    【結合テスト項番28】異常系 - 失敗した段階があれば、ERRORログにその段階名を出す
    前提条件：
    ・テスト用DB起動済み
    ・サービス層の日次処理が failed_steps=[due_soon] を返すよう差し替える
    入力値：scheduler.run_daily_job()
    想定結果：
    ・「失敗した段階があります」のログが1件（ERRORレベル）で、due_soon を含む
    ・DB接続が閉じられる
    """
    session = _FakeSession()
    monkeypatch.setattr(scheduler, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        services,
        "run_daily_job",
        lambda _db: {
            "no_show_cancel": 0,
            "unapproved_cancel": 0,
            "due_soon": 0,
            "overdue": 0,
            "failed_steps": ["due_soon"],
        },
    )
    with caplog.at_level(logging.INFO, logger="app"):
        scheduler.run_daily_job()

    records = [record for record in caplog.records if "失敗した段階があります" in record.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert "due_soon" in records[0].getMessage()
    assert session.closed is True


def test_scheduler_run_daily_job_swallows_unexpected_exception(monkeypatch, caplog):
    """
    【結合テスト項番29】セキュリティ・異常系 - 想定外の例外は外へ出さず、
      ログには例外の種類だけを出す
    前提条件：
    ・テスト用DB起動済み
    ・サービス層の日次処理が例外「secret-detail」を出すよう差し替える
    入力値：scheduler.run_daily_job()
    想定結果：
    ・例外は呼び出し元へ伝わらない
    ・DB接続が閉じられる
    ・ログに RuntimeError は出るが「secret-detail」は出ない
    """
    session = _FakeSession()
    monkeypatch.setattr(scheduler, "get_session_factory", lambda: lambda: session)

    def _raise(_db):
        raise RuntimeError("secret-detail")

    monkeypatch.setattr(services, "run_daily_job", _raise)
    with caplog.at_level(logging.ERROR, logger="app"):
        scheduler.run_daily_job()

    assert session.closed is True
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in messages
    assert "secret-detail" not in messages


def test_start_scheduler_registers_daily_job_at_six_jst(monkeypatch):
    """
    【結合テスト項番30】正常系 - 日次処理を毎日 JST 6:00 に、
      多重実行なし・取りこぼし6時間まで許容で登録し、停止すると解除される
    前提条件：
    ・テスト用DB起動済み
    ・スケジューラーが未起動
    入力値：start_scheduler() を実行し、登録内容を確認後、stop_scheduler() を実行
    想定結果：
    ・スケジューラーが動作中
    ・ジョブ daily_job が登録され、対象が run_daily_job、同時実行数=1、取りこぼし許容=21600秒、
        まとめて実行=有効
    ・実行時刻が6時0分、タイムゾーンが Asia/Tokyo
    ・停止後はスケジューラーが空になる
    """
    monkeypatch.setattr(scheduler, "_scheduler", None)
    scheduler.start_scheduler()
    try:
        registered = scheduler._scheduler
        assert registered is not None
        assert registered.running is True
        job = registered.get_job("daily_job")
        assert job is not None
        assert job.func is scheduler.run_daily_job
        assert job.max_instances == 1
        assert job.misfire_grace_time == 21600
        assert job.coalesce is True
        fields = {field.name: str(field) for field in job.trigger.fields}
        assert fields["hour"] == "6"
        assert fields["minute"] == "0"
        assert str(job.trigger.timezone) == "Asia/Tokyo"
    finally:
        scheduler.stop_scheduler()
    assert scheduler._scheduler is None


def test_start_scheduler_does_not_run_job_immediately(db, monkeypatch):
    """
    【結合テスト項番31】境界値 - 起動直後に日次処理を実行しない
    前提条件：
    ・テスト用DB起動済み
    ・スケジューラーが未起動。サービス層の日次処理が呼ばれたら記録するよう差し替える
    入力値：start_scheduler() の直後に stop_scheduler()
    想定結果：日次処理が1回も呼ばれない
    """
    called = []
    monkeypatch.setattr(services, "run_daily_job", lambda _db: called.append(True))
    monkeypatch.setattr(scheduler, "_scheduler", None)
    scheduler.start_scheduler()
    scheduler.stop_scheduler()
    assert called == []


def test_stop_scheduler_without_start_does_nothing(monkeypatch):
    """
    【結合テスト項番32】境界値 - 起動していない状態で停止してもエラーにならない
    前提条件：
    ・テスト用DB起動済み
    ・スケジューラーが未起動
    入力値：stop_scheduler()
    想定結果：エラーなし、スケジューラーは空のまま
    """
    monkeypatch.setattr(scheduler, "_scheduler", None)
    scheduler.stop_scheduler()
    assert scheduler._scheduler is None


def test_lifespan_starts_and_stops_scheduler(db, monkeypatch):
    """
    【結合テスト項番33】正常系 - アプリの起動でスケジューラーを開始し、終了で停止する
    前提条件：
    ・テスト用DB起動済み
    ・初期管理者の環境変数を設定。スケジューラーの開始・停止を記録するよう差し替える
    入力値：アプリを起動し、終了する
    想定結果：
    ・起動中は「開始」のみが記録される
    ・終了後は「開始」「停止」の順に記録される
    """
    monkeypatch.setenv("INITIAL_ADMIN_LOGIN_ID", "first_admin")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "InitialAdmin123")
    monkeypatch.setattr(main, "get_session_factory", lambda: lambda: nullcontext(db))
    events = []
    monkeypatch.setattr(scheduler, "start_scheduler", lambda: events.append("start"))
    monkeypatch.setattr(scheduler, "stop_scheduler", lambda: events.append("stop"))
    with TestClient(app):
        assert events == ["start"]
    assert events == ["start", "stop"]


class _FakeSession:
    """スケジューラーのテスト用セッション（閉じられたかどうかだけを記録する）"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True
