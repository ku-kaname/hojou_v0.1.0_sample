"""
結合テスト：備品管理（備品検索・取得・分類一覧・予約状況・登録・編集・CSV一括登録）

設計書：設計書/サーバー処理（main）/備品管理/、設計書/CRUD/備品管理/
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import auth, crud, main, services
from app.database import get_db
from app.main import app
from app.models import Equipment, LoanRequest, LoanStatus, User
from app.services import get_now, get_today

PASSWORD = "Password123"
CSV_HEADER = "資産番号,備品名,分類,説明,保管場所"


@pytest.fixture()
def client(db):
    """テスト用DBセッションを使うAPIクライアント"""
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_user(db, login_id, role="general", name="山田", must_change=False) -> User:
    user = User(
        login_id=login_id,
        name=name,
        password_hash=auth.hash_password(PASSWORD),
        role=role,
        must_change_password=must_change,
    )
    db.add(user)
    db.flush()
    return user


def _headers(user) -> dict[str, str]:
    token = auth.create_access_token(user.id, user.token_generation, get_now())
    return {"Authorization": f"Bearer {token}"}


def _make_equipment(db, asset_number, name="ノートPC", category="PC", is_active=True) -> Equipment:
    equipment = Equipment(asset_number=asset_number, name=name, category=category, is_active=is_active)
    db.add(equipment)
    db.flush()
    return equipment


def _make_loan(db, equipment, requester, status, start, due) -> LoanRequest:
    loan = LoanRequest(
        equipment_id=equipment.id,
        requester_id=requester.id,
        start_date=start,
        due_date=due,
        purpose="test",
        status=status,
        requested_at=get_now(),
    )
    db.add(loan)
    db.flush()
    return loan


def _csv_bytes(*lines, bom=False) -> bytes:
    text = "\r\n".join(lines) + "\r\n"
    encoded = text.encode("utf-8")
    if bom:
        return b"\xef\xbb\xbf" + encoded
    return encoded


def _import(client, admin, content):
    return client.post(
        "/api/admin/equipments/import",
        headers=_headers(admin),
        files={"file": ("any-name.txt", content, "application/octet-stream")},
    )


def _count_equipments(db) -> int:
    return db.execute(select(func.count()).select_from(Equipment)).scalar_one()


# ---- 認証・認可 ----


def test_equipment_endpoints_require_authentication(db, client):
    equipment = _make_equipment(db, "A-1")
    for path in (
        "/api/equipments",
        "/api/equipments/categories",
        f"/api/equipments/{equipment.id}",
        f"/api/equipments/{equipment.id}/reservations",
    ):
        response = client.get(path)
        assert response.status_code == 401


def test_equipment_endpoints_reject_user_who_must_change_password(db, client):
    user = _make_user(db, "taro", must_change=True)
    response = client.get("/api/equipments", headers=_headers(user))
    assert response.status_code == 403
    assert response.json() == {"detail": "初期パスワードの変更が必要です"}


def test_admin_equipment_endpoints_reject_general_user(db, client):
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    body = {"asset_number": "A-2", "name": "PC", "category": "PC"}
    update_body = {"name": "PC", "category": "PC", "description": "", "location": "", "is_active": True}
    responses = [
        client.post("/api/admin/equipments", headers=_headers(user), json=body),
        client.put(f"/api/admin/equipments/{equipment.id}", headers=_headers(user), json=update_body),
        _import(client, user, _csv_bytes(CSV_HEADER, "B-1,PC,PC,,")),
    ]
    for response in responses:
        assert response.status_code == 403
        assert response.json() == {"detail": "この操作を行う権限がありません"}


# ---- 備品一覧検索 ----


def test_list_equipments_general_user_sees_only_active_sorted_by_id(db, client):
    user = _make_user(db, "taro")
    first = _make_equipment(db, "A-1", name="ノートPC")
    _make_equipment(db, "A-2", name="旧PC", is_active=False)
    third = _make_equipment(db, "A-3", name="プロジェクター", category="AV")
    response = client.get("/api/equipments", headers=_headers(user))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert [item["id"] for item in body["items"]] == [first.id, third.id]
    item = body["items"][0]
    assert item["availability"] == "available"
    assert item["current_due_date"] is None
    assert item["is_overdue"] is False
    assert item["current_borrower_name"] is None
    assert item["created_at"].endswith("+09:00")


def test_list_equipments_filters_and_paging(db, client):
    user = _make_user(db, "taro")
    _make_equipment(db, "PC-001", name="ノートPC", category="PC")
    _make_equipment(db, "PC-002", name="デスクトップ", category="PC")
    _make_equipment(db, "AV-001", name="プロジェクター", category="AV")
    headers = _headers(user)
    by_keyword = client.get("/api/equipments", headers=headers, params={"keyword": "pc-00"}).json()
    assert by_keyword["total"] == 3 - 1  # AV-001は資産番号にも備品名にも含まれない
    by_name = client.get("/api/equipments", headers=headers, params={"keyword": "プロ"}).json()
    assert [item["asset_number"] for item in by_name["items"]] == ["AV-001"]
    by_category = client.get("/api/equipments", headers=headers, params={"category": "AV"}).json()
    assert [item["asset_number"] for item in by_category["items"]] == ["AV-001"]
    page_two = client.get("/api/equipments", headers=headers, params={"page": 2, "page_size": 2}).json()
    assert page_two["total"] == 3
    assert page_two["page"] == 2
    assert [item["asset_number"] for item in page_two["items"]] == ["AV-001"]
    empty = client.get("/api/equipments", headers=headers, params={"keyword": "存在しない"}).json()
    assert empty == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_list_equipments_keyword_wildcards_are_escaped(db, client):
    user = _make_user(db, "taro")
    _make_equipment(db, "A-1", name="100%達成")
    _make_equipment(db, "A-2", name="ふつうのPC")
    headers = _headers(user)
    percent = client.get("/api/equipments", headers=headers, params={"keyword": "%"}).json()
    assert [item["asset_number"] for item in percent["items"]] == ["A-1"]
    underscore = client.get("/api/equipments", headers=headers, params={"keyword": "_"}).json()
    assert underscore["total"] == 0


def test_list_equipments_availability_filter_and_lent_information(db, client):
    admin = _make_user(db, "admin", role="admin")
    borrower = _make_user(db, "borrower", name="貸出 太郎")
    today = get_today()
    lent = _make_equipment(db, "A-1")
    free = _make_equipment(db, "A-2")
    overdue = _make_equipment(db, "A-3")
    _make_loan(db, lent, borrower, LoanStatus.LENT.value, today - timedelta(days=1), today + timedelta(days=3))
    _make_loan(db, overdue, borrower, LoanStatus.LENT.value, today - timedelta(days=5), today - timedelta(days=2))
    # 返却済み・承認済みは貸出中として扱わない
    _make_loan(db, free, borrower, LoanStatus.RETURNED.value, today - timedelta(days=9), today - timedelta(days=8))
    _make_loan(db, free, borrower, LoanStatus.APPROVED.value, today + timedelta(days=5), today + timedelta(days=6))

    as_admin = client.get("/api/equipments", headers=_headers(admin)).json()
    by_asset = {item["asset_number"]: item for item in as_admin["items"]}
    assert by_asset["A-1"]["availability"] == "lent"
    assert by_asset["A-1"]["current_due_date"] == str(today + timedelta(days=3))
    assert by_asset["A-1"]["is_overdue"] is False
    assert by_asset["A-1"]["current_borrower_name"] == "貸出 太郎"
    assert by_asset["A-3"]["is_overdue"] is True
    assert by_asset["A-2"]["availability"] == "available"

    as_general = client.get("/api/equipments", headers=_headers(borrower)).json()
    assert all(item["current_borrower_name"] is None for item in as_general["items"])

    lent_only = client.get("/api/equipments", headers=_headers(admin), params={"availability": "lent"}).json()
    assert {item["asset_number"] for item in lent_only["items"]} == {"A-1", "A-3"}
    assert lent_only["total"] == 2
    available_only = client.get("/api/equipments", headers=_headers(admin), params={"availability": "available"}).json()
    assert [item["asset_number"] for item in available_only["items"]] == ["A-2"]

    invalid = client.get("/api/equipments", headers=_headers(admin), params={"availability": "unknown"})
    assert invalid.status_code == 422


def test_list_equipments_include_inactive_is_admin_only(db, client):
    admin = _make_user(db, "admin", role="admin")
    general = _make_user(db, "taro")
    _make_equipment(db, "A-1")
    _make_equipment(db, "A-2", is_active=False)
    denied = client.get("/api/equipments", headers=_headers(general), params={"include_inactive": "true"})
    assert denied.status_code == 403
    assert denied.json() == {"detail": "この操作を行う権限がありません"}
    allowed = client.get("/api/equipments", headers=_headers(admin), params={"include_inactive": "true"})
    assert allowed.status_code == 200
    assert allowed.json()["total"] == 2
    # 一般ユーザーでもFalseの明示指定は許可される
    explicit_false = client.get("/api/equipments", headers=_headers(general), params={"include_inactive": "false"})
    assert explicit_false.status_code == 200
    assert explicit_false.json()["total"] == 1


def test_list_equipments_rejects_invalid_paging(db, client):
    user = _make_user(db, "taro")
    headers = _headers(user)
    assert client.get("/api/equipments", headers=headers, params={"page": 0}).status_code == 422
    assert client.get("/api/equipments", headers=headers, params={"page_size": 101}).status_code == 422
    assert client.get("/api/equipments", headers=headers, params={"keyword": "a" * 51}).status_code == 422


# ---- 備品取得 ----


def test_get_equipment_returns_detail_with_lent_information(db, client):
    admin = _make_user(db, "admin", role="admin")
    borrower = _make_user(db, "borrower", name="貸出 太郎")
    today = get_today()
    equipment = _make_equipment(db, "A-1", name="ノートPC")
    equipment.description = "説明"
    equipment.location = "3F"
    _make_loan(db, equipment, borrower, LoanStatus.LENT.value, today - timedelta(days=5), today - timedelta(days=1))
    as_admin = client.get(f"/api/equipments/{equipment.id}", headers=_headers(admin))
    assert as_admin.status_code == 200
    body = as_admin.json()
    assert body["asset_number"] == "A-1"
    assert body["description"] == "説明"
    assert body["location"] == "3F"
    assert body["availability"] == "lent"
    assert body["is_overdue"] is True
    assert body["current_borrower_name"] == "貸出 太郎"
    as_general = client.get(f"/api/equipments/{equipment.id}", headers=_headers(borrower)).json()
    assert as_general["availability"] == "lent"
    assert as_general["current_borrower_name"] is None


def test_get_equipment_available_has_no_lent_information(db, client):
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    body = client.get(f"/api/equipments/{equipment.id}", headers=_headers(user)).json()
    assert body["availability"] == "available"
    assert body["current_due_date"] is None
    assert body["is_overdue"] is False
    assert body["current_borrower_name"] is None


def test_get_equipment_not_found_and_inactive_hidden_from_general_user(db, client):
    admin = _make_user(db, "admin", role="admin")
    general = _make_user(db, "taro")
    inactive = _make_equipment(db, "A-1", is_active=False)
    for path in (f"/api/equipments/{inactive.id}", "/api/equipments/999999"):
        response = client.get(path, headers=_headers(general))
        assert response.status_code == 404
        assert response.json() == {"detail": "備品が見つかりません"}
    as_admin = client.get(f"/api/equipments/{inactive.id}", headers=_headers(admin))
    assert as_admin.status_code == 200
    assert as_admin.json()["is_active"] is False
    assert client.get("/api/equipments/0", headers=_headers(admin)).status_code == 422
    assert client.get("/api/equipments/abc", headers=_headers(admin)).status_code == 422


# ---- 分類一覧取得 ----


def test_list_categories_returns_distinct_active_categories_sorted(db, client):
    user = _make_user(db, "taro")
    _make_equipment(db, "A-1", category="PC")
    _make_equipment(db, "A-2", category="PC")
    _make_equipment(db, "A-3", category="AV")
    _make_equipment(db, "A-4", category="廃止分類", is_active=False)
    response = client.get("/api/equipments/categories", headers=_headers(user))
    assert response.status_code == 200
    assert response.json() == {"items": ["AV", "PC"]}


def test_list_categories_empty(db, client):
    user = _make_user(db, "taro")
    response = client.get("/api/equipments/categories", headers=_headers(user))
    assert response.json() == {"items": []}


# ---- 予約状況取得 ----


def test_reservations_include_approved_and_lent_only_and_are_sorted(db, client):
    borrower = _make_user(db, "borrower", name="貸出 太郎")
    today = get_today()
    equipment = _make_equipment(db, "A-1")
    _make_loan(
        db, equipment, borrower, LoanStatus.APPROVED.value, today + timedelta(days=10), today + timedelta(days=12)
    )
    _make_loan(db, equipment, borrower, LoanStatus.LENT.value, today - timedelta(days=2), today + timedelta(days=3))
    # 対象外：申請中・却下・取消・返却済み
    _make_loan(
        db, equipment, borrower, LoanStatus.REQUESTED.value, today + timedelta(days=1), today + timedelta(days=2)
    )
    _make_loan(db, equipment, borrower, LoanStatus.REJECTED.value, today + timedelta(days=1), today + timedelta(days=2))
    _make_loan(db, equipment, borrower, LoanStatus.CANCELED.value, today + timedelta(days=1), today + timedelta(days=2))
    _make_loan(
        db, equipment, borrower, LoanStatus.RETURNED.value, today - timedelta(days=20), today - timedelta(days=15)
    )
    response = client.get(f"/api/equipments/{equipment.id}/reservations", headers=_headers(borrower))
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["status"] for item in items] == ["lent", "approved"]
    assert items[0]["start_date"] == str(today - timedelta(days=2))
    assert items[0]["due_date"] == str(today + timedelta(days=3))
    assert items[0]["occupied_until"] == str(today + timedelta(days=3))
    assert items[1]["occupied_until"] == str(today + timedelta(days=12))
    # 一般ユーザーには借用者氏名を返さない
    assert all(item["borrower_name"] is None for item in items)


def test_reservations_overdue_lent_is_occupied_until_today_and_borrower_visible_to_admin(db, client):
    admin = _make_user(db, "admin", role="admin")
    borrower = _make_user(db, "borrower", name="貸出 太郎")
    today = get_today()
    equipment = _make_equipment(db, "A-1")
    _make_loan(db, equipment, borrower, LoanStatus.LENT.value, today - timedelta(days=10), today - timedelta(days=3))
    response = client.get(f"/api/equipments/{equipment.id}/reservations", headers=_headers(admin))
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["due_date"] == str(today - timedelta(days=3))
    assert items[0]["occupied_until"] == str(today)
    assert items[0]["borrower_name"] == "貸出 太郎"


def test_reservations_exclude_ended_approved_but_keep_approved_ending_today(db, client):
    user = _make_user(db, "taro")
    today = get_today()
    equipment = _make_equipment(db, "A-1")
    _make_loan(db, equipment, user, LoanStatus.APPROVED.value, today - timedelta(days=5), today - timedelta(days=1))
    ending_today = _make_loan(db, equipment, user, LoanStatus.APPROVED.value, today - timedelta(days=0), today)
    response = client.get(f"/api/equipments/{equipment.id}/reservations", headers=_headers(user))
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["due_date"] == str(ending_today.due_date)


def test_reservations_empty_and_not_found_rules(db, client):
    admin = _make_user(db, "admin", role="admin")
    general = _make_user(db, "taro")
    inactive = _make_equipment(db, "A-1", is_active=False)
    active = _make_equipment(db, "A-2")
    assert client.get(f"/api/equipments/{active.id}/reservations", headers=_headers(general)).json() == {"items": []}
    denied = client.get(f"/api/equipments/{inactive.id}/reservations", headers=_headers(general))
    assert denied.status_code == 404
    assert denied.json() == {"detail": "備品が見つかりません"}
    assert client.get("/api/equipments/999999/reservations", headers=_headers(general)).status_code == 404
    as_admin = client.get(f"/api/equipments/{inactive.id}/reservations", headers=_headers(admin))
    assert as_admin.status_code == 200
    assert as_admin.json() == {"items": []}


# ---- 備品登録 ----


def test_create_equipment_success_defaults_and_trimming(db, client):
    admin = _make_user(db, "admin", role="admin")
    response = client.post(
        "/api/admin/equipments",
        headers=_headers(admin),
        json={"asset_number": "PC-001", "name": "  ノートPC  ", "category": " PC "},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["asset_number"] == "PC-001"
    assert body["name"] == "ノートPC"
    assert body["category"] == "PC"
    assert body["description"] == ""
    assert body["location"] == ""
    assert body["is_active"] is True
    assert body["availability"] == "available"
    assert body["current_due_date"] is None
    assert body["is_overdue"] is False
    assert body["current_borrower_name"] is None
    saved = db.get(Equipment, body["id"])
    assert saved is not None
    assert saved.asset_number == "PC-001"


def test_create_equipment_duplicate_asset_number_is_409_even_if_inactive(db, client):
    admin = _make_user(db, "admin", role="admin")
    _make_equipment(db, "PC-001", is_active=False)
    response = client.post(
        "/api/admin/equipments",
        headers=_headers(admin),
        json={"asset_number": "PC-001", "name": "PC", "category": "PC"},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "この資産番号は既に登録されています"}


def test_create_equipment_concurrent_duplicate_is_409(db, client, monkeypatch):
    admin = _make_user(db, "admin", role="admin")
    _make_equipment(db, "PC-001")
    # 事前確認をすり抜けた同時登録を再現するため、重複確認が「なし」を返すようにする
    monkeypatch.setattr(crud, "get_equipment_by_asset_number", lambda _db, _asset_number: None)
    response = client.post(
        "/api/admin/equipments",
        headers=_headers(admin),
        json={"asset_number": "PC-001", "name": "PC", "category": "PC"},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "この資産番号は既に登録されています"}


@pytest.mark.parametrize(
    "body",
    [
        {"asset_number": "", "name": "PC", "category": "PC"},
        {"asset_number": "a" * 33, "name": "PC", "category": "PC"},
        {"asset_number": "PC 001", "name": "PC", "category": "PC"},
        {"asset_number": "PC_001", "name": "PC", "category": "PC"},
        {"asset_number": "PC-001", "name": "   ", "category": "PC"},
        {"asset_number": "PC-001", "name": "n" * 101, "category": "PC"},
        {"asset_number": "PC-001", "name": "PC", "category": ""},
        {"asset_number": "PC-001", "name": "PC", "category": "c" * 51},
        {"asset_number": "PC-001", "name": "PC", "category": "PC", "description": "d" * 501},
        {"asset_number": "PC-001", "name": "PC", "category": "PC", "location": "l" * 101},
        {"name": "PC", "category": "PC"},
    ],
)
def test_create_equipment_validation_errors_are_422(db, client, body):
    admin = _make_user(db, "admin", role="admin")
    response = client.post("/api/admin/equipments", headers=_headers(admin), json=body)
    assert response.status_code == 422


# ---- 備品編集 ----


def _update_body(**overrides):
    body = {
        "name": "更新後",
        "category": "更新分類",
        "description": "更新説明",
        "location": "更新場所",
        "is_active": True,
    }
    body.update(overrides)
    return body


def test_update_equipment_success_keeps_asset_number(db, client):
    admin = _make_user(db, "admin", role="admin")
    equipment = _make_equipment(db, "PC-001")
    response = client.put(
        f"/api/admin/equipments/{equipment.id}",
        headers=_headers(admin),
        json=_update_body(name="  更新後  "),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["asset_number"] == "PC-001"
    assert body["name"] == "更新後"
    assert body["category"] == "更新分類"
    assert body["description"] == "更新説明"
    assert body["location"] == "更新場所"
    assert body["availability"] == "available"


def test_update_equipment_not_found_and_validation(db, client):
    admin = _make_user(db, "admin", role="admin")
    equipment = _make_equipment(db, "PC-001")
    missing = client.put("/api/admin/equipments/999999", headers=_headers(admin), json=_update_body())
    assert missing.status_code == 404
    assert missing.json() == {"detail": "備品が見つかりません"}
    invalid = client.put(f"/api/admin/equipments/{equipment.id}", headers=_headers(admin), json={"name": "PC"})
    assert invalid.status_code == 422
    zero = client.put("/api/admin/equipments/0", headers=_headers(admin), json=_update_body())
    assert zero.status_code == 422


@pytest.mark.parametrize(
    "status",
    [LoanStatus.REQUESTED.value, LoanStatus.APPROVED.value, LoanStatus.LENT.value],
)
def test_deactivate_equipment_with_open_loan_is_rejected(db, client, status):
    admin = _make_user(db, "admin", role="admin")
    borrower = _make_user(db, "borrower")
    today = get_today()
    equipment = _make_equipment(db, "PC-001")
    _make_loan(db, equipment, borrower, status, today, today + timedelta(days=1))
    response = client.put(
        f"/api/admin/equipments/{equipment.id}",
        headers=_headers(admin),
        json=_update_body(is_active=False),
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "申請中・承認済み・貸出中の申請がある備品は無効化できません"}
    db.refresh(equipment)
    assert equipment.is_active is True
    assert equipment.name == "ノートPC"


def test_deactivate_and_reactivate_equipment_without_open_loan(db, client):
    admin = _make_user(db, "admin", role="admin")
    borrower = _make_user(db, "borrower")
    today = get_today()
    equipment = _make_equipment(db, "PC-001")
    # 返却済み・却下・取消は未完了の申請ではない
    _make_loan(db, equipment, borrower, LoanStatus.RETURNED.value, today - timedelta(days=9), today - timedelta(days=8))
    _make_loan(db, equipment, borrower, LoanStatus.REJECTED.value, today, today + timedelta(days=1))
    _make_loan(db, equipment, borrower, LoanStatus.CANCELED.value, today, today + timedelta(days=1))
    headers = _headers(admin)
    deactivated = client.put(
        f"/api/admin/equipments/{equipment.id}", headers=headers, json=_update_body(is_active=False)
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    # 無効化済みは一般ユーザーから見えず、管理者は再有効化できる
    reactivated = client.put(
        f"/api/admin/equipments/{equipment.id}", headers=headers, json=_update_body(is_active=True)
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


def test_update_lent_equipment_content_returns_lent_information(db, client):
    admin = _make_user(db, "admin", role="admin")
    borrower = _make_user(db, "borrower", name="貸出 太郎")
    today = get_today()
    equipment = _make_equipment(db, "PC-001")
    _make_loan(db, equipment, borrower, LoanStatus.LENT.value, today - timedelta(days=3), today + timedelta(days=2))
    response = client.put(
        f"/api/admin/equipments/{equipment.id}",
        headers=_headers(admin),
        json=_update_body(name="貸出中でも更新可能"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "貸出中でも更新可能"
    assert body["availability"] == "lent"
    assert body["current_due_date"] == str(today + timedelta(days=2))
    assert body["current_borrower_name"] == "貸出 太郎"


def test_update_inactive_equipment_content_only_keeps_it_inactive(db, client):
    admin = _make_user(db, "admin", role="admin")
    equipment = _make_equipment(db, "PC-001", is_active=False)
    response = client.put(
        f"/api/admin/equipments/{equipment.id}",
        headers=_headers(admin),
        json=_update_body(is_active=False),
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False


# ---- 備品CSV一括登録 ----


def test_import_csv_success_with_bom_quotes_and_multiline_description(db, client):
    admin = _make_user(db, "admin", role="admin")
    content = _csv_bytes(
        CSV_HEADER,
        'PC-001,ノートPC,PC,"説明, カンマ入り",3F',
        "",
        'PC-002,"引用符付き備品名",PC,"1行目\r\n2行目",',
        " AV-001 , プロジェクター , AV , ,  ",
        bom=True,
    )
    response = _import(client, admin, content)
    assert response.status_code == 201
    assert response.json() == {"imported_count": 3}
    saved = {e.asset_number: e for e in db.execute(select(Equipment)).scalars().all()}
    assert saved["PC-001"].description == "説明, カンマ入り"
    assert saved["PC-001"].location == "3F"
    assert saved["PC-002"].description == "1行目\r\n2行目"
    assert saved["PC-002"].location == ""
    assert saved["AV-001"].name == "プロジェクター"
    assert saved["AV-001"].category == "AV"
    assert saved["AV-001"].description == ""
    assert all(e.is_active for e in saved.values())


def test_import_csv_size_and_emptiness_errors(db, client, monkeypatch):
    admin = _make_user(db, "admin", role="admin")
    empty = _import(client, admin, b"")
    assert empty.status_code == 400
    assert empty.json() == {"detail": "CSVファイルが空です"}
    monkeypatch.setattr(main, "CSV_MAX_BYTES", 100)
    too_large = _import(client, admin, b"x" * 101)
    assert too_large.status_code == 400
    assert too_large.json() == {"detail": "ファイルサイズが上限（5MB）を超えています"}
    just_limit = _import(client, admin, _csv_bytes(CSV_HEADER, "P-1,a,b,,").ljust(100, b" "))
    assert just_limit.status_code == 400 or just_limit.status_code == 201
    assert just_limit.json() != {"detail": "ファイルサイズが上限（5MB）を超えています"}


def test_import_csv_missing_file_part_is_422(db, client):
    admin = _make_user(db, "admin", role="admin")
    response = client.post("/api/admin/equipments/import", headers=_headers(admin))
    assert response.status_code == 422


def test_import_csv_file_level_errors(db, client):
    admin = _make_user(db, "admin", role="admin")
    not_utf8 = _import(client, admin, "資産番号,備品名,分類,説明,保管場所\r\n".encode("shift_jis"))
    assert not_utf8.status_code == 400
    assert not_utf8.json() == {"detail": "CSVの文字コードがUTF-8ではありません"}
    bad_header = _import(client, admin, _csv_bytes("資産番号,備品名,分類,説明", "A-1,PC,PC,"))
    assert bad_header.status_code == 400
    assert bad_header.json() == {"detail": "ヘッダー行が正しくありません（資産番号,備品名,分類,説明,保管場所）"}
    wrong_order = _import(client, admin, _csv_bytes("備品名,資産番号,分類,説明,保管場所", "PC,A-1,PC,,"))
    assert wrong_order.status_code == 400
    header_only = _import(client, admin, _csv_bytes(CSV_HEADER))
    assert header_only.status_code == 400
    assert header_only.json() == {"detail": "登録するデータ行がありません"}
    blank_only = _import(client, admin, _csv_bytes(CSV_HEADER, "", " , , , , "))
    assert blank_only.json() == {"detail": "登録するデータ行がありません"}
    blank_file = _import(client, admin, b"\n\n")
    assert blank_file.json() == {"detail": "ヘッダー行が正しくありません（資産番号,備品名,分類,説明,保管場所）"}
    unbalanced = _import(client, admin, _csv_bytes(CSV_HEADER, 'A-1,"PC,PC,,'))
    assert unbalanced.status_code == 400
    assert unbalanced.json() == {"detail": "CSVの形式が正しくありません"}
    assert _count_equipments(db) == 0


def test_import_csv_row_limit(db, client):
    admin = _make_user(db, "admin", role="admin")
    lines = [CSV_HEADER] + [f"A-{number},PC,PC,," for number in range(1001)]
    too_many = _import(client, admin, _csv_bytes(*lines))
    assert too_many.status_code == 400
    assert too_many.json() == {"detail": "データ行が上限（1,000行）を超えています"}
    lines_at_limit = [CSV_HEADER] + [f"A-{number},PC,PC,," for number in range(1000)]
    at_limit = _import(client, admin, _csv_bytes(*lines_at_limit))
    assert at_limit.status_code == 201
    assert at_limit.json() == {"imported_count": 1000}
    assert _count_equipments(db) == 1000


def test_import_csv_row_errors_are_collected_and_nothing_is_registered(db, client):
    admin = _make_user(db, "admin", role="admin")
    _make_equipment(db, "EXIST-1", is_active=False)
    content = _csv_bytes(
        CSV_HEADER,
        "OK-1,正常,PC,,",  # 2行目：正常
        "A-2,PC,PC",  # 3行目：列数不正
        ",PC,PC,,",  # 4行目：資産番号が空
        "B_5,PC,PC,,",  # 5行目：資産番号の形式
        "OK-1,重複,PC,,",  # 6行目：ファイル内重複
        "EXIST-1,既存,PC,,",  # 7行目：既存（無効化済み）
        f"C-8,{'n' * 101},PC,,",  # 8行目：備品名が長すぎる
        "D-9,PC,PC,,",  # 9行目：正常
        "E-10,\tタブ,PC,,",  # 10行目：タブは前後空白として除去され正常
        "F-11,途中\tタブ,PC,,",  # 11行目：制御文字
        f"G-12,PC,PC,{'d' * 501},",  # 12行目：説明が長すぎる
    )
    response = _import(client, admin, content)
    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "CSVの内容に誤りがあります"
    errors = [(e["row_number"], e["column"], e["message"]) for e in body["errors"]]
    assert errors == [
        (3, None, "列数が正しくありません"),
        (4, "資産番号", "資産番号は1〜32文字で入力してください"),
        (5, "資産番号", "資産番号は半角英数字と-のみ使用できます"),
        (6, "資産番号", "資産番号がファイル内で重複しています（2行目）"),
        (7, "資産番号", "資産番号が既に登録されています"),
        (8, "備品名", "備品名は1〜100文字で入力してください"),
        (11, "備品名", "備品名に使用できない文字が含まれています"),
        (12, "説明", "説明は0〜500文字で入力してください"),
    ]
    # 全件失敗：正常行も含め1件も登録されない
    assert _count_equipments(db) == 1


def test_import_csv_multiple_errors_in_one_row_and_multiline_row_numbers(db, client):
    admin = _make_user(db, "admin", role="admin")
    content = _csv_bytes(
        CSV_HEADER,
        'A-1,PC,PC,"説明\r\n複数行\r\nです",',  # 2〜4行目（正常）
        "A_2,,PC,,",  # 5行目：資産番号の形式と備品名の桁数
    )
    response = _import(client, admin, content)
    assert response.status_code == 400
    errors = [(e["row_number"], e["column"]) for e in response.json()["errors"]]
    assert errors == [(5, "資産番号"), (5, "備品名")]


def test_import_csv_description_allows_line_breaks_but_other_columns_do_not(db, client):
    admin = _make_user(db, "admin", role="admin")
    content = _csv_bytes(
        CSV_HEADER,
        'A-1,"名前\r\n改行",PC,,',
        'A-2,PC,PC,"説明\r\n改行","場所\r\n改行"',
    )
    response = _import(client, admin, content)
    assert response.status_code == 400
    errors = [(e["row_number"], e["column"], e["message"]) for e in response.json()["errors"]]
    assert errors == [
        (2, "備品名", "備品名に使用できない文字が含まれています"),
        (4, "保管場所", "保管場所に使用できない文字が含まれています"),
    ]


def test_import_csv_errors_are_capped_at_100_in_row_order(db, client):
    admin = _make_user(db, "admin", role="admin")
    lines = [CSV_HEADER] + [f"A_{number},PC,PC,," for number in range(150)]
    response = _import(client, admin, _csv_bytes(*lines))
    assert response.status_code == 400
    errors = response.json()["errors"]
    assert len(errors) == 100
    assert [e["row_number"] for e in errors] == list(range(2, 102))


def test_import_csv_concurrent_duplicate_is_409_and_registers_nothing(db, client, monkeypatch):
    admin = _make_user(db, "admin", role="admin")
    _make_equipment(db, "EXIST-1")
    # 事前確認をすり抜けた同時登録を再現するため、既存確認が「なし」を返すようにする
    monkeypatch.setattr(crud, "get_existing_asset_numbers", lambda _db, _asset_numbers: set())
    response = _import(client, admin, _csv_bytes(CSV_HEADER, "NEW-1,PC,PC,,", "EXIST-1,PC,PC,,"))
    assert response.status_code == 409
    assert response.json() == {"detail": "資産番号が既に登録されています。再度お試しください"}
    assert crud.get_equipment_by_asset_number(db, "NEW-1") is None


def test_import_csv_ignores_file_name_and_content_type(db, client):
    admin = _make_user(db, "admin", role="admin")
    response = client.post(
        "/api/admin/equipments/import",
        headers=_headers(admin),
        files={"file": ("../../evil.exe", _csv_bytes(CSV_HEADER, "A-1,PC,PC,,"), "application/x-msdownload")},
    )
    assert response.status_code == 201


# ---- CRUD・サービス層の補足 ----


def test_crud_existing_asset_numbers_and_empty_input(db):
    _make_equipment(db, "A-1")
    _make_equipment(db, "A-2", is_active=False)
    assert crud.get_existing_asset_numbers(db, []) == set()
    assert crud.get_existing_asset_numbers(db, ["A-1", "A-2", "A-3"]) == {"A-1", "A-2"}


def test_crud_equipment_lookup_and_lock(db):
    equipment = _make_equipment(db, "A-1", is_active=False)
    assert crud.get_equipment_by_id(db, equipment.id, False) is equipment
    assert crud.get_equipment_by_id(db, equipment.id, True) is equipment
    assert crud.get_equipment_by_id(db, equipment.id + 1000, False) is None
    assert crud.get_equipment_by_asset_number(db, "A-1") is equipment
    assert crud.get_equipment_by_asset_number(db, "NONE") is None


def test_crud_create_equipments_returns_count_and_sets_timestamps(db):
    now = get_now()
    rows = [
        crud.EquipmentCreateRow("B-1", "PC", "PC", "", ""),
        crud.EquipmentCreateRow("B-2", "AV", "AV", "説明", "3F"),
    ]
    count = crud.create_equipments(db, rows, now)
    assert count == 2
    saved = crud.get_equipment_by_asset_number(db, "B-2")
    assert saved is not None
    assert saved.is_active is True
    assert saved.created_at == now
    assert saved.updated_at == now
    duplicate = [crud.EquipmentCreateRow("B-1", "PC", "PC", "", "")]
    with pytest.raises(IntegrityError):
        crud.create_equipments(db, duplicate, now)


def test_crud_count_open_loans_and_lent_loan_lookup(db):
    user = _make_user(db, "taro", name="貸出 太郎")
    today = get_today()
    equipment = _make_equipment(db, "A-1")
    assert crud.count_open_loans_by_equipment(db, equipment.id) == 0
    assert crud.get_lent_loan_by_equipment(db, equipment.id) is None
    _make_loan(db, equipment, user, LoanStatus.RETURNED.value, today - timedelta(days=9), today - timedelta(days=8))
    _make_loan(db, equipment, user, LoanStatus.REQUESTED.value, today + timedelta(days=1), today + timedelta(days=2))
    lent = _make_loan(db, equipment, user, LoanStatus.LENT.value, today - timedelta(days=1), today)
    assert crud.count_open_loans_by_equipment(db, equipment.id) == 2
    lent_loan = crud.get_lent_loan_by_equipment(db, equipment.id)
    assert lent_loan is not None
    assert lent_loan[0] is lent
    assert lent_loan[1] == "貸出 太郎"


def test_service_reservation_status_values_and_today_boundary(db):
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today: date = services.get_today()
    _make_loan(db, equipment, user, LoanStatus.APPROVED.value, today, today)
    reservations = crud.get_reservations(db, equipment.id, today)
    assert len(reservations) == 1
    assert crud.get_reservations(db, equipment.id, today + timedelta(days=1)) == []
