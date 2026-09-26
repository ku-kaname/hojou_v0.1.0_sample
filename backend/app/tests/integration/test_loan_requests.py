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
    user = _make_user(db, "taro")
    user.must_change_password = True
    db.flush()
    response = client.get("/api/loan-requests/me", headers=_headers(user))
    assert response.status_code == 403
    assert response.json() == {"detail": "初期パスワードの変更が必要です"}


# ---- 貸出申請 ----


def test_apply_creates_requested_loan_and_notifies_other_admins(db, client):
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
    admin = _make_user(db, "admin1", role="admin")
    other = _make_user(db, "admin2", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, admin, equipment, today, today)
    assert response.status_code == 201
    notifications = _notifications(db, response.json()["id"])
    assert [n.recipient_id for n in notifications] == [other.id]


def test_apply_by_only_admin_creates_no_notification(db, client):
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, admin, equipment, today, today)
    assert response.status_code == 201
    assert _notifications(db, response.json()["id"]) == []


def test_apply_rejects_start_date_in_past(db, client):
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, user, equipment, today - timedelta(days=1), today)
    assert response.status_code == 400
    assert response.json() == {"detail": "開始日は今日以降を指定してください"}


def test_apply_rejects_due_date_before_start_date(db, client):
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, user, equipment, today + timedelta(days=2), today + timedelta(days=1))
    assert response.status_code == 400
    assert response.json() == {"detail": "返却予定日は開始日以降を指定してください"}


def test_apply_rejects_missing_or_inactive_equipment(db, client):
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
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    response = _apply(client, user, equipment, today, today, purpose="   ")
    assert response.status_code == 422
    response = _apply(client, user, equipment, today, today, purpose="あ" * 201)
    assert response.status_code == 422


@pytest.mark.parametrize("status", ["approved", "lent"])
def test_apply_rejects_overlap_with_approved_or_lent(db, client, status):
    user = _make_user(db, "taro")
    other = _make_user(db, "hanako")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, other, status, today + timedelta(days=2), today + timedelta(days=4))
    response = _apply(client, user, equipment, today + timedelta(days=4), today + timedelta(days=6))
    assert response.status_code == 409
    assert response.json() == {"detail": "指定した期間に承認済み・貸出中の予約があります"}


def test_apply_allows_adjacent_period_and_requested_overlap(db, client):
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
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, status, today - timedelta(days=1), today + timedelta(days=2))
    response = client.post(f"/api/loan-requests/{loan.id}/cancel", headers=_headers(user))
    assert response.status_code == 400
    assert response.json() == {"detail": "申請中・承認済みの申請のみ取り消せます"}


def test_cancel_own_loan_returns_404_for_other_users_or_missing(db, client):
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
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, user, "approved", today, today + timedelta(days=2))
    with pytest.raises(IntegrityError):
        _make_loan(db, equipment, user, "approved", today + timedelta(days=1), today + timedelta(days=3))
    db.rollback()


# ---- 申請却下 ----


def test_reject_success_notifies_requester_with_reason(db, client):
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
    admin = _make_user(db, "admin1", role="admin")
    response = client.post(
        "/api/admin/loan-requests/999999/admin-cancel", headers=_headers(admin), json={"reason": "理由"}
    )
    assert response.status_code == 404
    response = client.post("/api/admin/loan-requests/1/admin-cancel", headers=_headers(admin), json={"reason": " "})
    assert response.status_code == 422


# ---- 一覧・取得 ----


def test_admin_list_defaults_to_requested_oldest_first(db, client):
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
