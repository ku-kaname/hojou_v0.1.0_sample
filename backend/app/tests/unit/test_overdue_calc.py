"""
単体テスト：期限超過・占有終了日の算出

テスト仕様書：単体テスト仕様書/backend/貸出・返却・履歴/期限超過・占有終了日の算出
設計書：設計書/サーバー処理（main）/貸出申請・承認/承認待ち申請一覧取得、
        設計書/サーバー処理（main）/備品管理/備品取得、設計書/サーバー処理（main）/備品管理/予約状況取得
テスト対象ファイル：backend/app/services.py
  （_to_loan_request_response・_to_equipment_response・get_equipment_reservations）

【テストの考え方】
- データベースは使わない。申請・備品は必要な項目だけを持つ簡易オブジェクトで代用する。
- get_equipment_reservationsは、データベースにアクセスする3つの処理を偽物（モック）に差し替える。
- コメントの「項番」は、単体テスト仕様書の項番を表す。
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import services
from app.schemas import AuthenticatedUser

TODAY = date(2026, 9, 26)
CREATED_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)


def _loan(status: str, due_date: date, start_date: date = date(2026, 9, 1)) -> SimpleNamespace:
    """期限超過の算出に必要な項目を持つ申請を作る"""
    return SimpleNamespace(
        id=1,
        start_date=start_date,
        due_date=due_date,
        purpose="出張",
        status=status,
        reason="",
        return_note="",
        requested_at=CREATED_AT,
        decided_at=None,
        lent_at=None,
        returned_at=None,
        canceled_at=None,
    )


def _equipment() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        asset_number="A-001",
        name="ノートPC",
        category="PC",
        description="",
        location="",
        is_active=True,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


def _requester() -> SimpleNamespace:
    return SimpleNamespace(id=2, name="利用者", department="総務")


def _to_request_response(status: str, due_date: date):
    return services._to_loan_request_response(_loan(status, due_date), _equipment(), _requester(), TODAY)


# ---------------- _to_loan_request_response（項番1〜4） ----------------


def test_request_response_overdue():
    """項番1：貸出中で返却予定日が3日前なら、期限超過（真）・超過日数3"""
    response = _to_request_response("lent", TODAY - timedelta(days=3))
    assert response.is_overdue is True
    assert response.overdue_days == 3


def test_request_response_due_today_is_not_overdue():
    """項番2：貸出中でも返却予定日が今日なら、期限超過ではない（0日）"""
    response = _to_request_response("lent", TODAY)
    assert response.is_overdue is False
    assert response.overdue_days == 0


def test_request_response_due_tomorrow_is_not_overdue():
    """項番3：貸出中で返却予定日が翌日なら、期限超過ではない（0日）"""
    response = _to_request_response("lent", TODAY + timedelta(days=1))
    assert response.is_overdue is False
    assert response.overdue_days == 0


@pytest.mark.parametrize("status", ["approved", "returned"])
def test_request_response_not_lent_is_not_overdue(status):
    """項番4：返却予定日が過ぎていても、貸出中でなければ（承認済み・返却済み）期限超過ではない"""
    response = _to_request_response(status, TODAY - timedelta(days=3))
    assert response.is_overdue is False
    assert response.overdue_days == 0


# ---------------- _to_equipment_response（項番5〜9） ----------------


def test_equipment_response_no_lent_loan():
    """項番5：貸出中の申請がなければ、貸出可・返却予定日なし・借用者氏名なし"""
    response = services._to_equipment_response(_equipment(), None, None, TODAY, True)
    assert response.availability == "available"
    assert response.current_due_date is None
    assert response.is_overdue is False
    assert response.current_borrower_name is None


def test_equipment_response_due_today():
    """項番6：貸出中で返却予定日が今日なら、貸出中・期限超過ではない"""
    loan = _loan("lent", TODAY)
    response = services._to_equipment_response(_equipment(), loan, "利用者", TODAY, True)
    assert response.availability == "lent"
    assert response.current_due_date == TODAY
    assert response.is_overdue is False


def test_equipment_response_due_yesterday():
    """項番7：貸出中で返却予定日が前日なら、期限超過"""
    loan = _loan("lent", TODAY - timedelta(days=1))
    response = services._to_equipment_response(_equipment(), loan, "利用者", TODAY, True)
    assert response.availability == "lent"
    assert response.is_overdue is True


def test_equipment_response_borrower_name_by_role():
    """項番8：借用者氏名は管理者にだけ設定し、一般ユーザーにはNoneにする"""
    loan = _loan("lent", TODAY)
    admin_response = services._to_equipment_response(_equipment(), loan, "利用者", TODAY, True)
    general_response = services._to_equipment_response(_equipment(), loan, "利用者", TODAY, False)
    assert admin_response.current_borrower_name == "利用者"
    assert general_response.current_borrower_name is None


def test_equipment_response_borrower_name_ignored_without_loan():
    """項番9：貸出中の申請がなければ、借用者氏名を渡されても管理者でもNoneにする"""
    response = services._to_equipment_response(_equipment(), None, "利用者", TODAY, True)
    assert response.current_borrower_name is None


# ---------------- get_equipment_reservations（項番10〜17） ----------------


def _user(role: str) -> AuthenticatedUser:
    return AuthenticatedUser(id=1, login_id="user1", name="山田", role=role, must_change_password=False)


@pytest.fixture
def stub_reservations(monkeypatch):
    """備品の確認・今日・予約期間一覧を差し替え、指定した予約一覧を返すようにする"""

    def install(reservations: list[tuple[SimpleNamespace, str | None]]) -> None:
        monkeypatch.setattr(services, "_get_visible_equipment", lambda db, equipment_id, user: None)
        monkeypatch.setattr(services, "get_today", lambda: TODAY)
        monkeypatch.setattr(services.crud, "get_reservations", lambda db, equipment_id, today: reservations)

    return install


def _get_items(role: str = "admin"):
    response = services.get_equipment_reservations(None, _user(role), 1)
    return response.items


def test_reservations_approved_future(stub_reservations):
    """項番10：承認済みで返却予定日が今日より後なら、占有終了日は返却予定日"""
    due = TODAY + timedelta(days=3)
    stub_reservations([(_loan("approved", due, start_date=TODAY + timedelta(days=1)), None)])
    item = _get_items()[0]
    assert item.occupied_until == due
    assert item.status == "approved"


def test_reservations_lent_future(stub_reservations):
    """項番11：貸出中で返却予定日が今日以降なら、占有終了日は返却予定日"""
    due = TODAY + timedelta(days=2)
    stub_reservations([(_loan("lent", due), None)])
    item = _get_items()[0]
    assert item.occupied_until == due
    assert item.status == "lent"


def test_reservations_lent_due_today(stub_reservations):
    """項番12：貸出中で返却予定日が今日なら、占有終了日は返却予定日（今日）"""
    stub_reservations([(_loan("lent", TODAY), None)])
    assert _get_items()[0].occupied_until == TODAY


def test_reservations_lent_overdue_is_occupied_until_today(stub_reservations):
    """項番13：貸出中で返却予定日を過ぎていれば、まだ戻っていないため占有終了日は今日"""
    stub_reservations([(_loan("lent", TODAY - timedelta(days=2)), None)])
    assert _get_items()[0].occupied_until == TODAY


def test_reservations_approved_past_due_keeps_due_date(stub_reservations):
    """項番14：承認済み（未貸出）で返却予定日が過去なら、占有終了日は返却予定日のまま（今日にしない）"""
    due = TODAY - timedelta(days=2)
    stub_reservations([(_loan("approved", due, start_date=TODAY - timedelta(days=5)), None)])
    assert _get_items()[0].occupied_until == due


def test_reservations_borrower_name_by_role(stub_reservations):
    """項番15：借用者氏名は管理者にだけ返し、一般ユーザーにはNoneにする"""
    stub_reservations([(_loan("lent", TODAY + timedelta(days=1)), "利用者")])
    assert _get_items("admin")[0].borrower_name == "利用者"
    assert _get_items("general")[0].borrower_name is None


def test_reservations_empty(stub_reservations):
    """項番16：予約がなければ、空の一覧（空配列）を返す"""
    stub_reservations([])
    assert _get_items() == []


def test_reservations_keep_order(stub_reservations):
    """項番17：取得した順（開始日の昇順）のまま2件返す"""
    first = _loan("lent", TODAY + timedelta(days=1), start_date=TODAY - timedelta(days=1))
    second = _loan("approved", TODAY + timedelta(days=5), start_date=TODAY + timedelta(days=2))
    stub_reservations([(first, None), (second, None)])
    items = _get_items()
    assert [item.start_date for item in items] == [first.start_date, second.start_date]
    assert [item.status for item in items] == ["lent", "approved"]
