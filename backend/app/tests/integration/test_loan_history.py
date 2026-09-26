"""
結合テスト：貸出・返却・履歴（貸出・返却・貸出履歴検索・貸出履歴CSV出力・期限超過一覧取得）

設計書：設計書/サーバー処理（main）/貸出・返却・履歴/、設計書/CRUD/貸出・返却・履歴/
"""

import csv
import io
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app import auth, crud, services
from app.database import get_db
from app.main import app
from app.models import Equipment, LoanRequest, User
from app.schemas import JST
from app.services import get_now, get_today

PASSWORD = "Password123"


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


def _make_loan(
    db, equipment, requester, status, start, due, lent_at=None, returned_at=None, note="", purpose="test"
) -> LoanRequest:
    loan = LoanRequest(
        equipment_id=equipment.id,
        requester_id=requester.id,
        start_date=start,
        due_date=due,
        purpose=purpose,
        status=status,
        requested_at=get_now(),
        lent_at=lent_at,
        returned_at=returned_at,
        return_note=note,
    )
    db.add(loan)
    db.flush()
    return loan


def _jst_datetime(day, hour=12, minute=0) -> datetime:
    """JSTの日時（指定日の指定時刻）を作る"""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)


def _lend_url(loan) -> str:
    return f"/api/admin/loan-requests/{loan.id}/lend"


def _return_url(loan) -> str:
    return f"/api/admin/loan-requests/{loan.id}/return"


# ---- 認証・権限 ----


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/admin/loan-requests/1/lend"),
        ("post", "/api/admin/loan-requests/1/return"),
        ("get", "/api/admin/loan-history"),
        ("get", "/api/admin/loan-history/export"),
        ("get", "/api/admin/loan-requests/overdue"),
    ],
)
def test_endpoints_require_authentication_and_admin(db, client, method, path):
    general = _make_user(db, "taro")
    assert getattr(client, method)(path).status_code == 401
    assert getattr(client, method)(path, headers=_headers(general)).status_code == 403


# ---- 貸出 ----


def test_lend_updates_status_and_records_admin_and_time(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today + timedelta(days=3))

    response = client.post(_lend_url(loan), headers=_headers(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == loan.id
    assert body["status"] == "lent"
    db.refresh(loan)
    assert loan.status == "lent"
    assert loan.lent_by == admin.id
    assert loan.lent_at is not None


def test_lend_is_allowed_after_start_date(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today - timedelta(days=2), today)
    assert client.post(_lend_url(loan), headers=_headers(admin)).status_code == 200


def test_lend_not_found(db, client):
    admin = _make_user(db, "admin1", role="admin")
    response = client.post("/api/admin/loan-requests/999999/lend", headers=_headers(admin))
    assert response.status_code == 404
    assert response.json() == {"detail": "申請が見つかりません"}


@pytest.mark.parametrize("status", ["requested", "rejected", "canceled", "lent", "returned"])
def test_lend_rejects_status_other_than_approved(db, client, status):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, status, today, today)
    response = client.post(_lend_url(loan), headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {"detail": "承認済みの申請のみ貸出処理できます"}


def test_lend_rejects_before_start_date(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today + timedelta(days=1), today + timedelta(days=2))
    response = client.post(_lend_url(loan), headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {"detail": "開始日前の申請は貸出処理できません"}


def test_lend_rejects_after_due_date(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today - timedelta(days=3), today - timedelta(days=1))
    response = client.post(_lend_url(loan), headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {"detail": "返却予定日を過ぎた申請は貸出処理できません"}


def test_lend_rejects_inactive_equipment(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1", is_active=False)
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)
    response = client.post(_lend_url(loan), headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {"detail": "無効化された備品は貸出処理できません"}


def test_lend_rejects_when_another_loan_is_lent(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, user, "lent", today - timedelta(days=5), today - timedelta(days=1), lent_at=get_now())
    loan = _make_loan(db, equipment, user, "approved", today, today + timedelta(days=1))
    response = client.post(_lend_url(loan), headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {
        "detail": "この備品は貸出中の別の申請があるため貸出処理できません（先に返却処理を行ってください）"
    }


def test_lend_converts_unique_violation_to_409(db, client, monkeypatch):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)

    def fake_lend(*args, **kwargs):
        raise IntegrityError("stmt", {}, Exception("uq_loan_request_equipment_lent"))

    monkeypatch.setattr(crud, "lend_loan_request", fake_lend)
    response = client.post(_lend_url(loan), headers=_headers(admin))
    assert response.status_code == 409
    assert response.json() == {"detail": "この備品は既に貸出中です"}


def test_lend_converts_commit_integrity_error_to_409(db, client, monkeypatch):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "approved", today, today)

    def fake_commit():
        raise IntegrityError("stmt", {}, Exception("uq_loan_request_equipment_lent"))

    monkeypatch.setattr(db, "commit", fake_commit)
    response = client.post(_lend_url(loan), headers=_headers(admin))
    assert response.status_code == 409


# ---- 返却 ----


def test_return_updates_status_and_records_note(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "lent", today, today + timedelta(days=1), lent_at=get_now())

    response = client.post(_return_url(loan), json={"return_note": "  傷あり  "}, headers=_headers(admin))
    assert response.status_code == 200
    assert response.json()["status"] == "returned"
    db.refresh(loan)
    assert loan.status == "returned"
    assert loan.returned_by == admin.id
    assert loan.returned_at is not None
    assert loan.return_note == "傷あり"


def test_return_without_body_uses_empty_note(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "lent", today, today, lent_at=get_now())
    response = client.post(_return_url(loan), headers=_headers(admin))
    assert response.status_code == 200
    db.refresh(loan)
    assert loan.return_note == ""


def test_return_allows_overdue_loan(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(
        db, equipment, user, "lent", today - timedelta(days=5), today - timedelta(days=2), lent_at=get_now()
    )
    assert client.post(_return_url(loan), headers=_headers(admin)).status_code == 200


def test_return_rejects_too_long_note(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, "lent", today, today, lent_at=get_now())
    response = client.post(_return_url(loan), json={"return_note": "あ" * 201}, headers=_headers(admin))
    assert response.status_code == 422


def test_return_not_found(db, client):
    admin = _make_user(db, "admin1", role="admin")
    response = client.post("/api/admin/loan-requests/999999/return", headers=_headers(admin))
    assert response.status_code == 404
    assert response.json() == {"detail": "申請が見つかりません"}


@pytest.mark.parametrize("status", ["requested", "approved", "rejected", "canceled", "returned"])
def test_return_rejects_status_other_than_lent(db, client, status):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    loan = _make_loan(db, equipment, user, status, today, today)
    response = client.post(_return_url(loan), headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {"detail": "貸出中の申請のみ返却処理できます"}


def test_lend_after_return_of_previous_loan_succeeds(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    first = _make_loan(
        db, equipment, user, "lent", today - timedelta(days=3), today - timedelta(days=1), lent_at=get_now()
    )
    second = _make_loan(db, equipment, user, "approved", today, today + timedelta(days=1))
    assert client.post(_lend_url(second), headers=_headers(admin)).status_code == 400
    assert client.post(_return_url(first), headers=_headers(admin)).status_code == 200
    assert client.post(_lend_url(second), headers=_headers(admin)).status_code == 200


# ---- 貸出履歴検索 ----


def test_history_returns_only_lent_and_returned_newest_first(db, client):
    user = _make_user(db, "taro", name="山田太郎", department="営業部")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    other = _make_equipment(db, "A-2")
    today = get_today()
    old = _make_loan(
        db,
        equipment,
        user,
        "returned",
        today - timedelta(days=9),
        today - timedelta(days=7),
        lent_at=_jst_datetime(today - timedelta(days=9)),
        returned_at=_jst_datetime(today - timedelta(days=8)),
        note="問題なし",
    )
    new = _make_loan(
        db,
        other,
        user,
        "lent",
        today - timedelta(days=1),
        today + timedelta(days=1),
        lent_at=_jst_datetime(today - timedelta(days=1)),
    )
    _make_loan(db, equipment, user, "requested", today + timedelta(days=5), today + timedelta(days=6))
    _make_loan(db, equipment, user, "approved", today + timedelta(days=7), today + timedelta(days=8))
    _make_loan(db, equipment, user, "rejected", today + timedelta(days=9), today + timedelta(days=9))
    _make_loan(db, equipment, user, "canceled", today + timedelta(days=10), today + timedelta(days=10))

    response = client.get("/api/admin/loan-history", headers=_headers(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [new.id, old.id]
    first = body["items"][0]
    assert first["equipment_asset_number"] == "A-2"
    assert first["requester_name"] == "山田太郎"
    assert first["requester_department"] == "営業部"
    assert first["status"] == "lent"
    assert first["returned_at"] is None
    assert first["lent_at"].endswith("+09:00")
    assert body["items"][1]["status"] == "returned"
    assert body["items"][1]["return_note"] == "問題なし"


def test_history_delay_days_for_lent_and_returned(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    other = _make_equipment(db, "A-2")
    third = _make_equipment(db, "A-3")
    today = get_today()
    lent_late = _make_loan(
        db,
        equipment,
        user,
        "lent",
        today - timedelta(days=6),
        today - timedelta(days=3),
        lent_at=_jst_datetime(today - timedelta(days=6)),
    )
    returned_late = _make_loan(
        db,
        other,
        user,
        "returned",
        today - timedelta(days=9),
        today - timedelta(days=6),
        lent_at=_jst_datetime(today - timedelta(days=9)),
        returned_at=_jst_datetime(today - timedelta(days=4)),
    )
    returned_on_time = _make_loan(
        db,
        third,
        user,
        "returned",
        today - timedelta(days=9),
        today - timedelta(days=5),
        lent_at=_jst_datetime(today - timedelta(days=9)),
        returned_at=_jst_datetime(today - timedelta(days=7)),
    )
    response = client.get("/api/admin/loan-history", headers=_headers(admin))
    delays = {item["id"]: item["delay_days"] for item in response.json()["items"]}
    assert delays[lent_late.id] == 3
    assert delays[returned_late.id] == 2
    assert delays[returned_on_time.id] == 0


def test_history_delay_days_uses_jst_date_of_returned_at(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    due = today - timedelta(days=3)
    # 返却日時はJSTの翌日0:30（UTCでは前日15:30）。JSTの日付で遅延日数を数える
    returned_at = _jst_datetime(due + timedelta(days=1), hour=0, minute=30)
    loan = _make_loan(
        db,
        equipment,
        user,
        "returned",
        due - timedelta(days=2),
        due,
        lent_at=_jst_datetime(due - timedelta(days=2)),
        returned_at=returned_at,
    )
    response = client.get("/api/admin/loan-history", headers=_headers(admin))
    assert response.json()["items"][0]["id"] == loan.id
    assert response.json()["items"][0]["delay_days"] == 1


def test_history_filters_by_date_range_in_jst(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    day = today - timedelta(days=10)
    edge_start = _make_loan(
        db,
        equipment,
        user,
        "returned",
        day,
        day + timedelta(days=1),
        lent_at=_jst_datetime(day, hour=0, minute=0),
        returned_at=_jst_datetime(day, hour=1),
    )
    edge_end = _make_loan(
        db,
        equipment,
        user,
        "returned",
        day,
        day + timedelta(days=1),
        lent_at=_jst_datetime(day, hour=23, minute=59),
        returned_at=_jst_datetime(day + timedelta(days=1)),
    )
    before = _make_loan(
        db,
        equipment,
        user,
        "returned",
        day,
        day,
        lent_at=_jst_datetime(day - timedelta(days=1), hour=23, minute=59),
        returned_at=_jst_datetime(day),
    )
    after = _make_loan(
        db,
        equipment,
        user,
        "returned",
        day,
        day,
        lent_at=_jst_datetime(day + timedelta(days=1), hour=0, minute=0),
        returned_at=_jst_datetime(day + timedelta(days=1), hour=1),
    )
    response = client.get(
        "/api/admin/loan-history",
        params={"from_date": day.isoformat(), "to_date": day.isoformat()},
        headers=_headers(admin),
    )
    ids = {item["id"] for item in response.json()["items"]}
    assert ids == {edge_start.id, edge_end.id}
    assert before.id not in ids
    assert after.id not in ids


def test_history_filters_by_equipment_and_requester(db, client):
    user = _make_user(db, "taro")
    other_user = _make_user(db, "hanako", name="鈴木")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    other_equipment = _make_equipment(db, "A-2")
    today = get_today()
    lent_at = _jst_datetime(today - timedelta(days=1))
    target = _make_loan(db, equipment, user, "lent", today, today, lent_at=lent_at)
    _make_loan(db, other_equipment, user, "lent", today, today, lent_at=lent_at)
    _make_loan(db, equipment, other_user, "returned", today, today, lent_at=lent_at, returned_at=lent_at)

    response = client.get(
        "/api/admin/loan-history",
        params={"equipment_id": equipment.id, "requester_id": user.id},
        headers=_headers(admin),
    )
    assert [item["id"] for item in response.json()["items"]] == [target.id]

    response = client.get("/api/admin/loan-history", params={"equipment_id": 999999}, headers=_headers(admin))
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_history_paging(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    today = get_today()
    for index in range(3):
        equipment = _make_equipment(db, f"A-{index}")
        _make_loan(db, equipment, user, "lent", today, today, lent_at=_jst_datetime(today - timedelta(days=index + 1)))
    response = client.get("/api/admin/loan-history", params={"page": 2, "page_size": 2}, headers=_headers(admin))
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert len(body["items"]) == 1


def test_history_keeps_deactivated_equipment_and_user(db, client):
    user = _make_user(db, "taro", name="退職者")
    user.is_active = False
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1", is_active=False)
    today = get_today()
    _make_loan(db, equipment, user, "returned", today, today, lent_at=get_now(), returned_at=get_now())
    response = client.get("/api/admin/loan-history", headers=_headers(admin))
    item = response.json()["items"][0]
    assert item["status"] == "returned"
    assert item["requester_name"] == "退職者"


def test_history_rejects_reversed_date_range(db, client):
    admin = _make_user(db, "admin1", role="admin")
    today = get_today()
    params = {"from_date": today.isoformat(), "to_date": (today - timedelta(days=1)).isoformat()}
    response = client.get("/api/admin/loan-history", params=params, headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {"detail": "貸出日終了は貸出日開始以降を指定してください"}


@pytest.mark.parametrize(
    "params",
    [{"from_date": "abc"}, {"equipment_id": 0}, {"requester_id": 0}, {"page": 0}, {"page_size": 101}],
)
def test_history_validates_query(db, client, params):
    admin = _make_user(db, "admin1", role="admin")
    assert client.get("/api/admin/loan-history", params=params, headers=_headers(admin)).status_code == 422


# ---- 貸出履歴CSV出力 ----


def _parse_csv(response) -> list[list[str]]:
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def test_export_returns_csv_with_header_and_rows(db, client):
    user = _make_user(db, "taro", name="山田太郎", department="営業部")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1", name="ノートPC")
    today = get_today()
    lent_at = _jst_datetime(today - timedelta(days=3), hour=9, minute=5)
    returned_at = _jst_datetime(today - timedelta(days=1), hour=18, minute=30)
    _make_loan(
        db,
        equipment,
        user,
        "returned",
        today - timedelta(days=3),
        today - timedelta(days=2),
        lent_at=lent_at,
        returned_at=returned_at,
        note="キズ, あり",
        purpose="出張",
    )
    response = client.get("/api/admin/loan-history/export", headers=_headers(admin))
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    filename = "loan_history_" + today.strftime("%Y%m%d") + ".csv"
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'
    assert b"\r\n" in response.content
    rows = _parse_csv(response)
    assert rows[0] == [
        "申請ID",
        "資産番号",
        "備品名",
        "借用者氏名",
        "借用者所属",
        "開始日",
        "返却予定日",
        "用途",
        "状態",
        "貸出日時",
        "返却日時",
        "遅延日数",
        "返却時状態メモ",
    ]
    assert len(rows) == 2
    row = rows[1]
    assert row[1:5] == ["A-1", "ノートPC", "山田太郎", "営業部"]
    assert row[5] == (today - timedelta(days=3)).isoformat()
    assert row[6] == (today - timedelta(days=2)).isoformat()
    assert row[7] == "出張"
    assert row[8] == "返却済み"
    assert row[9] == lent_at.strftime("%Y-%m-%d %H:%M:%S")
    assert row[10] == returned_at.strftime("%Y-%m-%d %H:%M:%S")
    assert row[11] == "1"
    assert row[12] == "キズ, あり"


def test_export_shows_lent_status_with_blank_return_and_delay(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(
        db,
        equipment,
        user,
        "lent",
        today - timedelta(days=5),
        today - timedelta(days=2),
        lent_at=_jst_datetime(today - timedelta(days=5)),
    )
    row = _parse_csv(client.get("/api/admin/loan-history/export", headers=_headers(admin)))[1]
    assert row[8] == "貸出中"
    assert row[10] == ""
    assert row[11] == "2"


def test_export_header_only_when_no_rows(db, client):
    admin = _make_user(db, "admin1", role="admin")
    response = client.get("/api/admin/loan-history/export", params={"equipment_id": 999999}, headers=_headers(admin))
    assert response.status_code == 200
    assert len(_parse_csv(response)) == 1


def test_export_neutralizes_csv_injection(db, client):
    user = _make_user(db, "taro", name="=HYPERLINK(1)", department="+cmd")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1", name="@SUM(1)")
    today = get_today()
    _make_loan(
        db,
        equipment,
        user,
        "returned",
        today,
        today,
        lent_at=get_now(),
        returned_at=get_now(),
        note="-1+1",
        purpose="\tタブ",
    )
    row = _parse_csv(client.get("/api/admin/loan-history/export", headers=_headers(admin)))[1]
    assert row[2] == "'@SUM(1)"
    assert row[3] == "'=HYPERLINK(1)"
    assert row[4] == "'+cmd"
    assert row[7] == "'\tタブ"
    assert row[12] == "'-1+1"


def test_export_applies_same_filters_as_search(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    other = _make_equipment(db, "A-2")
    today = get_today()
    lent_at = _jst_datetime(today - timedelta(days=1))
    _make_loan(db, equipment, user, "lent", today, today, lent_at=lent_at)
    _make_loan(db, other, user, "lent", today, today, lent_at=lent_at)
    response = client.get(
        "/api/admin/loan-history/export", params={"equipment_id": equipment.id}, headers=_headers(admin)
    )
    rows = _parse_csv(response)
    assert len(rows) == 2
    assert rows[1][1] == "A-1"


def test_export_rejects_reversed_date_range(db, client):
    admin = _make_user(db, "admin1", role="admin")
    today = get_today()
    params = {"from_date": today.isoformat(), "to_date": (today - timedelta(days=1)).isoformat()}
    response = client.get("/api/admin/loan-history/export", params=params, headers=_headers(admin))
    assert response.status_code == 400


def test_export_rejects_over_limit(db, client, monkeypatch):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    for index in range(3):
        _make_loan(
            db,
            equipment,
            user,
            "returned",
            today,
            today,
            lent_at=_jst_datetime(today - timedelta(days=index + 1)),
            returned_at=get_now(),
        )
    monkeypatch.setattr(services, "_LOAN_HISTORY_CSV_MAX_ROWS", 2)
    response = client.get("/api/admin/loan-history/export", headers=_headers(admin))
    assert response.status_code == 400
    assert response.json() == {"detail": "出力対象が上限（10,000件）を超えています。絞り込み条件を指定してください"}

    monkeypatch.setattr(services, "_LOAN_HISTORY_CSV_MAX_ROWS", 3)
    assert client.get("/api/admin/loan-history/export", headers=_headers(admin)).status_code == 200


def test_export_logs_count_without_personal_data(db, client, caplog):
    user = _make_user(db, "taro", name="秘密の氏名")
    admin = _make_user(db, "admin1", role="admin")
    equipment = _make_equipment(db, "A-1")
    today = get_today()
    _make_loan(db, equipment, user, "lent", today, today, lent_at=get_now())
    with caplog.at_level("INFO", logger="app"):
        client.get("/api/admin/loan-history/export", headers=_headers(admin))
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "貸出履歴CSV出力" in messages
    assert "出力件数=1" in messages
    assert "秘密の氏名" not in messages


# ---- 期限超過一覧 ----


def test_overdue_lists_only_lent_past_due_oldest_first(db, client):
    user = _make_user(db, "taro")
    admin = _make_user(db, "admin1", role="admin")
    today = get_today()
    newer = _make_loan(
        db,
        _make_equipment(db, "A-1"),
        user,
        "lent",
        today - timedelta(days=5),
        today - timedelta(days=1),
        lent_at=get_now(),
    )
    older = _make_loan(
        db,
        _make_equipment(db, "A-2"),
        user,
        "lent",
        today - timedelta(days=9),
        today - timedelta(days=4),
        lent_at=get_now(),
    )
    _make_loan(db, _make_equipment(db, "A-3"), user, "lent", today - timedelta(days=1), today, lent_at=get_now())
    _make_loan(
        db,
        _make_equipment(db, "A-4"),
        user,
        "returned",
        today - timedelta(days=9),
        today - timedelta(days=8),
        lent_at=get_now(),
        returned_at=get_now(),
    )
    _make_loan(db, _make_equipment(db, "A-5"), user, "approved", today - timedelta(days=9), today - timedelta(days=8))

    response = client.get("/api/admin/loan-requests/overdue", headers=_headers(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [older.id, newer.id]
    assert body["items"][0]["is_overdue"] is True


def test_overdue_paging_and_validation(db, client):
    admin = _make_user(db, "admin1", role="admin")
    response = client.get("/api/admin/loan-requests/overdue", params={"page_size": 1}, headers=_headers(admin))
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert (
        client.get("/api/admin/loan-requests/overdue", params={"page": 0}, headers=_headers(admin)).status_code == 422
    )
