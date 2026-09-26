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
    """
    【結合テスト項番1】異常系 - 認証なしは401（備品の参照系4エンドポイント共通）
    前提条件：
    ・テスト用DB起動済み
    ・備品（A-1）が登録済み
    入力値：トークンなしで GET /api/equipments・/api/equipments/categories・/api/equipments/{ID}・/a
      pi/equipments/{ID}/reservations
    想定結果：4件とも HTTP 401
    """
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
    """
    【結合テスト項番2】異常系 - 初期パスワード未変更のユーザーは利用できない
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro・初期パスワード変更要）が登録済み・トークン取得済み
    入力値：GET /api/equipments（Bearer 認証あり）
    想定結果：
    ・HTTP 403
    ・detail が "初期パスワードの変更が必要です"
    """
    user = _make_user(db, "taro", must_change=True)
    response = client.get("/api/equipments", headers=_headers(user))
    assert response.status_code == 403
    assert response.json() == {"detail": "初期パスワードの変更が必要です"}


def test_admin_equipment_endpoints_reject_general_user(db, client):
    """
    【結合テスト項番18】異常系 - 一般ユーザーは管理者用の備品登録・編集・CSV一括登録を使えない
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み。備品（A-1）が登録済み
    入力値：一般ユーザーで POST /api/admin/equipments・PUT /api/admin/equipments/{ID}・POST /api/adm
      in/equipments/import
    想定結果：
    ・3件とも HTTP 403
    ・detail が "この操作を行う権限がありません"
    """
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
    """
    【結合テスト項番3】正常系 - 一般ユーザーには有効な備品だけをID昇順で返し、貸出状況の項目を返す
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品3件（有効2件・無効化済み1件）が登録済みで、貸出はない
    入力値：GET /api/equipments（クエリなし）
    想定結果：
    ・HTTP 200
    ・total=2・page=1・page_size=20
    ・有効な備品2件だけがID昇順で返り、無効化済みは含まれない
    ・1件目：availability=available、current_due_date=null、is_overdue=false、
        current_borrower_name=null、created_at が +09:00
    """
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
    """
    【結合テスト項番4】正常系 - キーワード・分類で絞り込み、ページングができる。該当なしは空
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品3件（PC-001 ノートPC／PC、PC-002 デスクトップ／PC、AV-001 プロジェクター／AV）
        が登録済み（絞り込み対象外のデータが存在する）
    入力値：
    ・keyword=pc-00（大文字小文字を区別しない）
    ・keyword=プロ
    ・category=AV
    ・page=2&page_size=2
    ・keyword=存在しない
    想定結果：
    ・資産番号に一致する PC-001・PC-002 の2件（total=2）
    ・備品名に一致する AV-001 だけ
    ・分類が AV の AV-001 だけ
    ・total=3・page=2・AV-001 だけ
    ・空（items=[]・total=0・page=1・page_size=20）
    """
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
    """
    【結合テスト項番5】セキュリティ - キーワードのLIKEワイルドカード（%・_）を文字として扱う
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品2件（A-1「100%達成」・A-2「ふつうのPC」）が登録済み
    入力値：
    ・keyword=%
    ・keyword=_
    想定結果：
    ・%を含む A-1 だけが返る（全件にならない）
    ・_を含む備品はなく total=0
    """
    user = _make_user(db, "taro")
    _make_equipment(db, "A-1", name="100%達成")
    _make_equipment(db, "A-2", name="ふつうのPC")
    headers = _headers(user)
    percent = client.get("/api/equipments", headers=headers, params={"keyword": "%"}).json()
    assert [item["asset_number"] for item in percent["items"]] == ["A-1"]
    underscore = client.get("/api/equipments", headers=headers, params={"keyword": "_"}).json()
    assert underscore["total"] == 0


def test_list_equipments_availability_filter_and_lent_information(db, client):
    """
    【結合テスト項番6】正常系・異常系 - 貸出状況の判定・絞り込み。借用者名は管理者のみ表示。
      不正値は422
    前提条件：
    ・テスト用DB起動済み
    ・管理者と借用者（貸出 太郎）が登録済み。A-1（貸出中・期限内）、A-2（返却済みと承認済みのみ）、
        A-3（貸出中・期限超過）の3件が存在する
    入力値：
    ・管理者で全件取得
    ・一般ユーザー（借用者）で全件取得
    ・availability=lent（管理者）
    ・availability=available（管理者）
    ・availability=unknown
    想定結果：
    ・A-1：lent・current_due_date が返却予定日・is_overdue=false・借用者名あり。
        A-3：is_overdue=true。A-2：available（返却済み・承認済みは貸出中扱いにならない）
    ・一般ユーザーでは全件の current_borrower_name が null
    ・A-1・A-3 の2件（total=2）
    ・A-2 だけ
    ・HTTP 422
    """
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
    """
    【結合テスト項番7】異常系・正常系 - 無効化済みを含める指定は管理者のみ
    前提条件：
    ・テスト用DB起動済み
    ・管理者と一般ユーザーが登録済み。備品は有効1件・無効化済み1件
    入力値：
    ・一般ユーザーが include_inactive=true
    ・管理者が include_inactive=true
    ・一般ユーザーが include_inactive=false
    想定結果：
    ・一般ユーザーは HTTP 403（detail が「この操作を行う権限がありません」）
    ・管理者は HTTP 200・total=2
    ・一般ユーザーの false 指定は HTTP 200・total=1
    """
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
    """
    【結合テスト項番8】異常系 - 入力チェックエラー（page が0・page_size が101・keyword が51文字）
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    入力値：
    ・page=0
    ・page_size=101
    ・keyword が51文字
    想定結果：3件とも HTTP 422
    """
    user = _make_user(db, "taro")
    headers = _headers(user)
    assert client.get("/api/equipments", headers=headers, params={"page": 0}).status_code == 422
    assert client.get("/api/equipments", headers=headers, params={"page_size": 101}).status_code == 422
    assert client.get("/api/equipments", headers=headers, params={"keyword": "a" * 51}).status_code == 422


# ---- 備品取得 ----


def test_get_equipment_returns_detail_with_lent_information(db, client):
    """
    【結合テスト項番9】正常系 - 備品の詳細と貸出状況を返す。借用者名は管理者のみ
    前提条件：
    ・テスト用DB起動済み
    ・管理者と借用者（貸出 太郎）が登録済み。備品（A-1・説明あり・保管場所3F）が貸出中（期限超過）
    入力値：
    ・管理者で GET /api/equipments/{ID}
    ・借用者（一般ユーザー）で同じ備品を取得
    想定結果：
    ・管理者：HTTP 200、asset_number・description・location が登録値、availability=lent、
        is_overdue=true、current_borrower_name=貸出 太郎
    ・一般ユーザー：availability=lent、current_borrower_name=null
    """
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
    """
    【結合テスト項番10】正常系 - 貸出中でない備品は貸出状況の項目が空
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品（A-1）が登録済みで貸出はない
    入力値：GET /api/equipments/{ID}
    想定結果：availability=available、current_due_date=null、is_overdue=false、
      current_borrower_name=null
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    body = client.get(f"/api/equipments/{equipment.id}", headers=_headers(user)).json()
    assert body["availability"] == "available"
    assert body["current_due_date"] is None
    assert body["is_overdue"] is False
    assert body["current_borrower_name"] is None


def test_get_equipment_not_found_and_inactive_hidden_from_general_user(db, client):
    """
    【結合テスト項番11】異常系 - 存在しない備品・無効化済みの備品（一般ユーザー）は404。
      管理者は無効化済みも取得できる。ID形式不正は422
    前提条件：
    ・テスト用DB起動済み
    ・管理者と一般ユーザー（taro）が登録済み・トークン取得済み。無効化済みの備品が存在する
    入力値：
    ・一般ユーザーが無効化済みの備品・ID 999999 を取得
    ・管理者が無効化済みの備品を取得
    ・管理者が ID 0・abc を取得
    想定結果：
    ・404、detail が "備品が見つかりません"
    ・HTTP 200、is_active=false
    ・HTTP 422
    """
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
    """
    【結合テスト項番12】正常系 - 有効な備品の分類を重複なし・昇順で返す
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品4件（PC×2・AV×1・無効化済みで分類「廃止分類」×1）が登録済み
    入力値：GET /api/equipments/categories
    想定結果：
    ・HTTP 200、{"items": ["AV", "PC"]}（重複なし。無効化済みの備品の分類は含まれない）
    """
    user = _make_user(db, "taro")
    _make_equipment(db, "A-1", category="PC")
    _make_equipment(db, "A-2", category="PC")
    _make_equipment(db, "A-3", category="AV")
    _make_equipment(db, "A-4", category="廃止分類", is_active=False)
    response = client.get("/api/equipments/categories", headers=_headers(user))
    assert response.status_code == 200
    assert response.json() == {"items": ["AV", "PC"]}


def test_list_categories_empty(db, client):
    """
    【結合テスト項番13】正常系 - 備品がなければ空
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品が1件もない
    入力値：GET /api/equipments/categories
    想定結果：{"items": []}
    """
    user = _make_user(db, "taro")
    response = client.get("/api/equipments/categories", headers=_headers(user))
    assert response.json() == {"items": []}


# ---- 予約状況取得 ----


def test_reservations_include_approved_and_lent_only_and_are_sorted(db, client):
    """
    【結合テスト項番14】正常系 - 承認済み・貸出中だけを開始日順で返し、
      一般ユーザーには借用者名を返さない
    前提条件：
    ・テスト用DB起動済み
    ・借用者（一般ユーザー）が登録済み。備品に承認済み・貸出中の申請と、
        対象外の申請中・却下・取消・返却済みの申請が各1件ある
    入力値：GET /api/equipments/{ID}/reservations（一般ユーザー）
    想定結果：
    ・HTTP 200
    ・貸出中→承認済みの順（開始日順）の2件だけ
    ・貸出中：start_date・due_date・occupied_until（返却予定日）が正しい
    ・承認済み：occupied_until が返却予定日
    ・全件の borrower_name が null
    """
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
    """
    【結合テスト項番15】正常系 - 期限超過の貸出中は今日まで占有。借用者名は管理者のみ表示
    前提条件：
    ・テスト用DB起動済み
    ・管理者と借用者（貸出 太郎）が登録済み。備品が貸出中で返却予定日が3日前
    入力値：GET /api/equipments/{ID}/reservations（管理者）
    想定結果：
    ・1件返る
    ・due_date が3日前、occupied_until が今日
    ・borrower_name が「貸出 太郎」
    """
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
    """
    【結合テスト項番16】境界値 - 終了済みの承認済みは除外し、今日が返却予定日の承認済みは含める
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    ・備品に承認済みの申請が2件（返却予定日が昨日／今日）ある
    入力値：GET /api/equipments/{ID}/reservations
    想定結果：返却予定日が今日の1件だけが返る（昨日終了の分は含まれない）
    """
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
    """
    【結合テスト項番17】正常系・異常系 - 予約がなければ空。
      存在しない・無効化済みの備品は一般ユーザーだと404、管理者は取得できる
    前提条件：
    ・テスト用DB起動済み
    ・管理者と一般ユーザーが登録済み。有効な備品（予約なし）と無効化済みの備品がある
    入力値：
    ・一般ユーザーが有効な備品の予約状況を取得
    ・一般ユーザーが無効化済みの備品・ID 999999 を取得
    ・管理者が無効化済みの備品を取得
    想定結果：
    ・{"items": []}
    ・404、detail が "備品が見つかりません"
    ・HTTP 200・{"items": []}
    """
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
    """
    【結合テスト項番19】正常系 - 備品を登録し、前後の空白を除去し、省略した項目に既定値を設定する
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：POST /api/admin/equipments {"asset_number": "PC-001", "name": "  ノートPC  ", "category"
      : " PC "}
    想定結果：
    ・HTTP 201
    ・name=ノートPC・category=PC（前後の空白を除去）、description・location が空文字、is_active=true
    ・availability=available、current_due_date=null、is_overdue=false、current_borrower_name=null
    ・DBに asset_number=PC-001 で保存される
    """
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
    """
    【結合テスト項番20】異常系 - 資産番号が重複（無効化済みの備品の資産番号も重複扱い）
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・無効化済みの備品（PC-001）が登録済み
    入力値：asset_number=PC-001 で登録
    想定結果：
    ・HTTP 409
    ・detail が "この資産番号は既に登録されています"
    """
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
    """
    【結合テスト項番21】異常系 - 同時登録で事前確認をすり抜けた重複もDBの一意制約で409にする
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・備品（PC-001）が登録済み。事前の重複確認が「なし」を返すよう差し替える
    入力値：asset_number=PC-001 で登録
    想定結果：
    ・HTTP 409
    ・detail が "この資産番号は既に登録されています"
    """
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
    """
    【結合テスト項番22】異常系 - 入力チェックエラー（パラメーター化11件）
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：
    ・資産番号が空
    ・資産番号が33文字
    ・資産番号に空白
    ・資産番号にアンダースコア
    ・備品名が空白のみ
    ・備品名が101文字
    ・分類が空
    ・分類が51文字
    ・説明が501文字
    ・保管場所が101文字
    ・資産番号の欠落
    想定結果：11件とも HTTP 422
    """
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
    """
    【結合テスト項番23】正常系 - 備品を編集できる。資産番号は変更されない
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・備品（PC-001）が登録済み
    入力値：PUT /api/admin/equipments/{ID} name=「  更新後  」・category・description・location・is_
      active=true
    想定結果：
    ・HTTP 200
    ・asset_number は PC-001 のまま
    ・name=更新後（空白を除去）、category・description・location が更新値
    ・availability=available
    """
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
    """
    【結合テスト項番24】異常系 - 存在しない備品は404、必須項目の欠落・ID 0 は422
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・備品（PC-001）が登録済み
    入力値：
    ・ID 999999 で更新
    ・本文が name のみ
    ・ID 0 で更新
    想定結果：
    ・HTTP 404、detail が "備品が見つかりません"
    ・HTTP 422
    ・HTTP 422
    """
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
    """
    【結合テスト項番25】異常系 - 申請中・承認済み・貸出中の申請がある備品は無効化できない（パラメー
      ター化3件）
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・借用者が登録済み。備品に、申請中／承認済み／貸出中のいずれかの申請が1件ある
    入力値：is_active=false で更新（備品名も変更する）
    想定結果：
    ・HTTP 400
    ・detail が "申請中・承認済み・貸出中の申請がある備品は無効化できません"
    ・備品は有効のまま、備品名も変更されない
    """
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
    """
    【結合テスト項番26】正常系 - 返却済み・却下・取消の申請だけなら無効化でき、再有効化もできる
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・借用者が登録済み。備品に返却済み・却下・取消の申請が各1件ある
    入力値：
    ・is_active=false で更新
    ・is_active=true で更新
    想定結果：
    ・HTTP 200、is_active=false
    ・HTTP 200、is_active=true
    """
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
    """
    【結合テスト項番27】正常系 - 貸出中の備品も内容を更新でき、貸出状況を返す
    前提条件：
    ・テスト用DB起動済み
    ・管理者と借用者（貸出 太郎）が登録済み。備品が貸出中
    入力値：name を変更して更新（is_active=true）
    想定結果：
    ・HTTP 200、name が更新される
    ・availability=lent、current_due_date が返却予定日、current_borrower_name=貸出 太郎
    """
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
    """
    【結合テスト項番28】正常系 - 無効化済みの備品を内容だけ更新しても無効のまま
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・無効化済みの備品（PC-001）が登録済み
    入力値：is_active=false のまま内容を更新
    想定結果：
    ・HTTP 200
    ・is_active=false のまま
    """
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
    """
    【結合テスト項番29】正常系 - BOM付き・引用符・空行・説明の改行・前後の空白を含むCSVを一括登録す
      る
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：multipart で file にCSV（BOM付き。3行のデータ＋空行1行。説明にカンマ・改行を含む。
      空白付きの行を含む）を送信
    想定結果：
    ・HTTP 201、imported_count=3
    ・DBの説明・保管場所が入力どおり（カンマ・改行を保持）
    ・前後の空白は除去され、説明が空の項目は空文字
    ・登録した全件が有効
    """
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
    """
    【結合テスト項番30】異常系・境界値 - 空ファイル・サイズ上限超過は400。
      上限ちょうどは超過扱いにならない
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・サイズ上限を100バイトに差し替える（後半は、有効なCSV1件のバイト数に差し替える）
    入力値：
    ・0バイトのファイル
    ・101バイトのファイル
    ・上限ちょうどのバイト数の有効なCSV（1件）
    ・上限より1バイト多い同じCSV
    想定結果：
    ・HTTP 400、detail が "CSVファイルが空です"
    ・HTTP 400、detail が "ファイルサイズが上限（5MB）を超えています"
    ・HTTP 201（正常に登録される）
    ・HTTP 400、detail が "ファイルサイズが上限（5MB）を超えています"
    """
    admin = _make_user(db, "admin", role="admin")
    empty = _import(client, admin, b"")
    assert empty.status_code == 400
    assert empty.json() == {"detail": "CSVファイルが空です"}
    monkeypatch.setattr(main, "CSV_MAX_BYTES", 100)
    too_large = _import(client, admin, b"x" * 101)
    assert too_large.status_code == 400
    assert too_large.json() == {"detail": "ファイルサイズが上限（5MB）を超えています"}
    # 上限ちょうどのサイズ（上限と同じバイト数）は受け付けられる
    limit_csv = _csv_bytes(CSV_HEADER, "P-1,a,b,,")
    monkeypatch.setattr(main, "CSV_MAX_BYTES", len(limit_csv))
    just_limit = _import(client, admin, limit_csv)
    assert just_limit.status_code == 201
    # 上限より1バイト多いと拒否される
    over_limit = _import(client, admin, limit_csv + b" ")
    assert over_limit.status_code == 400
    assert over_limit.json() == {"detail": "ファイルサイズが上限（5MB）を超えています"}


def test_import_csv_missing_file_part_is_422(db, client):
    """
    【結合テスト項番31】異常系 - ファイル項目（file）の欠落は422
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：file を付けずに POST
    想定結果：HTTP 422
    """
    admin = _make_user(db, "admin", role="admin")
    response = client.post("/api/admin/equipments/import", headers=_headers(admin))
    assert response.status_code == 422


def test_import_csv_file_level_errors(db, client):
    """
    【結合テスト項番32】異常系 - ファイル全体のエラー（文字コード・ヘッダー・データ行なし・形式不正）
      は400で1件も登録しない
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：
    ・Shift_JIS のCSV
    ・ヘッダーが4列
    ・ヘッダーの列の順序違い
    ・ヘッダーのみ
    ・空行・空白だけの行のみ
    ・改行だけのファイル
    ・引用符が閉じていない行
    想定結果：
    ・各 HTTP 400、detail：「CSVの文字コードがUTF-8ではありません」／
        「ヘッダー行が正しくありません（資産番号,備品名,分類,説明,保管場所）」
        （列不足・順序違い・改行だけ）／「登録するデータ行がありません」（ヘッダーのみ・空行のみ）／
        「CSVの形式が正しくありません」（引用符不正）
    ・DBの備品は0件のまま
    """
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
    """
    【結合テスト項番33】境界値 - データ行の上限（1,000行）
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：
    ・データ行1,001行
    ・データ行1,000行
    想定結果：
    ・1,001行：HTTP 400、detail が "データ行が上限（1,000行）を超えています"
    ・1,000行：HTTP 201、imported_count=1000、DBに1,000件登録される
    """
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
    """
    【結合テスト項番34】異常系 - 行ごとのエラーをまとめて返し、正常な行も含め1件も登録しない
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・無効化済みの備品（EXIST-1）が登録済み
    入力値：正常行・列数不正・資産番号が空・資産番号の形式不正・ファイル内重複・既存の資産番号・備品
      名が長すぎる・タブ（前後は除去され正常）・途中のタブ・説明が長すぎる を含むCSV
    想定結果：
    ・HTTP 400、detail が「CSVの内容に誤りがあります」
    ・errors に（行番号・項目・メッセージ）が行順に8件：3行目 列数、4行目 資産番号（1〜32文字）、
        5行目 資産番号（形式）、6行目 資産番号（ファイル内重複・2行目）、7行目 資産番号（既に登録）、
        8行目 備品名（長さ）、11行目 備品名（使用できない文字）、12行目 説明（長さ）
    ・DBの備品は事前の1件のまま（正常行も登録されない）
    """
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
    """
    【結合テスト項番35】異常系 - 1行に複数のエラーがあれば全て返す。
      説明の改行を含む行の行番号が実際の行番号になる
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：説明が3行にわたる正常行（2〜4行目）の次の行（5行目）に、
      資産番号の形式不正と備品名が空の行
    想定結果：
    ・HTTP 400
    ・errors が（5行目・資産番号）（5行目・備品名）の2件
    """
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
    """
    【結合テスト項番36】異常系 - 改行を使えるのは説明だけ（備品名・保管場所の改行は不可）
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：備品名に改行を含む行と、説明・保管場所に改行を含む行
    想定結果：
    ・HTTP 400
    ・errors が（2行目・備品名・使用できない文字）（4行目・保管場所・使用できない文字）
        の2件（説明の改行はエラーにならない）
    """
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
    """
    【結合テスト項番37】境界値 - エラーは最大100件まで、行の順に返す
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：資産番号の形式不正の行が150行あるCSV
    想定結果：
    ・HTTP 400
    ・errors が100件で、行番号が2〜101
    """
    admin = _make_user(db, "admin", role="admin")
    lines = [CSV_HEADER] + [f"A_{number},PC,PC,," for number in range(150)]
    response = _import(client, admin, _csv_bytes(*lines))
    assert response.status_code == 400
    errors = response.json()["errors"]
    assert len(errors) == 100
    assert [e["row_number"] for e in errors] == list(range(2, 102))


def test_import_csv_concurrent_duplicate_is_409_and_registers_nothing(db, client, monkeypatch):
    """
    【結合テスト項番38】異常系 - 同時登録で事前確認をすり抜けた重複は409で、1件も登録しない
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    ・備品（EXIST-1）が登録済み。事前の既存確認が「なし」を返すよう差し替える
    入力値：NEW-1 と EXIST-1 を含むCSV
    想定結果：
    ・HTTP 409
    ・detail が "資産番号が既に登録されています。再度お試しください"
    ・NEW-1 は登録されない
    """
    admin = _make_user(db, "admin", role="admin")
    _make_equipment(db, "EXIST-1")
    # 事前確認をすり抜けた同時登録を再現するため、既存確認が「なし」を返すようにする
    monkeypatch.setattr(crud, "get_existing_asset_numbers", lambda _db, _asset_numbers: set())
    response = _import(client, admin, _csv_bytes(CSV_HEADER, "NEW-1,PC,PC,,", "EXIST-1,PC,PC,,"))
    assert response.status_code == 409
    assert response.json() == {"detail": "資産番号が既に登録されています。再度お試しください"}
    assert crud.get_equipment_by_asset_number(db, "NEW-1") is None


def test_import_csv_ignores_file_name_and_content_type(db, client):
    """
    【結合テスト項番39】セキュリティ - ファイル名・Content-Type に依存せず内容だけで処理する
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin）が登録済み・トークン取得済み
    入力値：ファイル名「../../evil.exe」・Content-Type「application/x-msdownload」で正しいCSVを送信
    想定結果：HTTP 201（ファイル名・形式は判定に使われず、パス操作も起きない）
    """
    admin = _make_user(db, "admin", role="admin")
    response = client.post(
        "/api/admin/equipments/import",
        headers=_headers(admin),
        files={"file": ("../../evil.exe", _csv_bytes(CSV_HEADER, "A-1,PC,PC,,"), "application/x-msdownload")},
    )
    assert response.status_code == 201


# ---- CRUD・サービス層の補足 ----


def test_crud_existing_asset_numbers_and_empty_input(db):
    """
    【結合テスト項番40】DBアクセス - 既存の資産番号の検索（無効化済みを含む）。空の入力は空
    前提条件：
    ・テスト用DB起動済み
    ・備品2件（A-1 有効・A-2 無効化済み）が登録済み
    入力値：
    ・空のリスト
    ・A-1・A-2・A-3 を指定
    想定結果：
    ・空の集合
    ・A-1・A-2 の集合（無効化済みも含む。存在しないA-3は含まない）
    """
    _make_equipment(db, "A-1")
    _make_equipment(db, "A-2", is_active=False)
    assert crud.get_existing_asset_numbers(db, []) == set()
    assert crud.get_existing_asset_numbers(db, ["A-1", "A-2", "A-3"]) == {"A-1", "A-2"}


def test_crud_equipment_lookup_and_lock(db):
    """
    【結合テスト項番41】DBアクセス - 備品をIDまたは資産番号で検索する
    前提条件：
    ・テスト用DB起動済み
    ・無効化済みの備品（A-1）が登録済み
    入力値：
    ・get_equipment_by_id（行ロックなし・あり・存在しないID）
    ・get_equipment_by_asset_number（A-1・存在しない番号）
    想定結果：
    ・登録済みの備品が返り、存在しないIDは None
    ・A-1 は見つかり、存在しない番号は None
    """
    equipment = _make_equipment(db, "A-1", is_active=False)
    assert crud.get_equipment_by_id(db, equipment.id, False) is equipment
    assert crud.get_equipment_by_id(db, equipment.id, True) is equipment
    assert crud.get_equipment_by_id(db, equipment.id + 1000, False) is None
    assert crud.get_equipment_by_asset_number(db, "A-1") is equipment
    assert crud.get_equipment_by_asset_number(db, "NONE") is None


def test_crud_create_equipments_returns_count_and_sets_timestamps(db):
    """
    【結合テスト項番42】DBアクセス - 備品の一括登録は件数を返し、作成・更新日時を設定する。
      重複は一意制約違反
    前提条件：
    ・テスト用DB起動済み
    入力値：
    ・2件（B-1・B-2）を一括登録
    ・既存のB-1を再度登録
    想定結果：
    ・件数2、B-2 が有効・作成日時と更新日時が指定した日時
    ・IntegrityError
    """
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
    """
    【結合テスト項番43】DBアクセス - 未完了の申請の件数と、貸出中の申請・借用者名の取得
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（貸出 太郎）と備品が登録済み
    入力値：
    ・申請がない状態で件数・貸出中の申請を取得
    ・返却済み・申請中・貸出中の申請を追加して再取得
    想定結果：
    ・件数0・貸出中の申請なし
    ・未完了は申請中と貸出中の2件、貸出中の申請と借用者名「貸出 太郎」が取得できる
    """
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
    """
    【結合テスト項番44】サービス層 - 今日が終了日の承認済みの予約は、今日は含み、翌日は含まない
    前提条件：
    ・テスト用DB起動済み
    ・備品と承認済みの申請（開始日・返却予定日とも今日）が登録済み
    入力値：
    ・基準日を今日にして予約を取得
    ・基準日を翌日にして予約を取得
    想定結果：
    ・1件
    ・0件
    """
    user = _make_user(db, "taro")
    equipment = _make_equipment(db, "A-1")
    today: date = services.get_today()
    _make_loan(db, equipment, user, LoanStatus.APPROVED.value, today, today)
    reservations = crud.get_reservations(db, equipment.id, today)
    assert len(reservations) == 1
    assert crud.get_reservations(db, equipment.id, today + timedelta(days=1)) == []
