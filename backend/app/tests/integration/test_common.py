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
from sqlalchemy.exc import IntegrityError

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
    db.add(User(login_id="bad id!", name="x", password_hash="x"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_overlapping_approved_loans_rejected(db):
    user = _create_user(db)
    equipment = _create_equipment(db)
    _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "approved")
    with pytest.raises(IntegrityError):
        _create_loan(db, user, equipment, date(2026, 10, 5), date(2026, 10, 8), "approved")


def test_overlapping_requested_loans_allowed(db):
    user = _create_user(db)
    equipment = _create_equipment(db)
    _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "requested")
    _create_loan(db, user, equipment, date(2026, 10, 3), date(2026, 10, 8), "requested")


def test_adjacent_period_allowed(db):
    user = _create_user(db)
    equipment = _create_equipment(db)
    _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "approved")
    _create_loan(db, user, equipment, date(2026, 10, 6), date(2026, 10, 8), "approved")


def test_invalid_period_rejected(db):
    user = _create_user(db)
    equipment = _create_equipment(db)
    with pytest.raises(IntegrityError):
        _create_loan(db, user, equipment, date(2026, 10, 5), date(2026, 10, 1), "requested")


def test_create_notification_normal_allows_duplicates(db):
    user = _create_user(db)
    equipment = _create_equipment(db)
    loan = _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "requested")
    assert crud.create_notification(db, user.id, "approved", loan.id, TODAY) is True
    assert crud.create_notification(db, user.id, "approved", loan.id, TODAY) is True
    assert _count_notifications(db) == 2


def test_create_notification_daily_is_idempotent(db):
    user = _create_user(db)
    equipment = _create_equipment(db)
    loan = _create_loan(db, user, equipment, date(2026, 10, 1), date(2026, 10, 5), "lent")
    assert crud.create_notification(db, user.id, "overdue", loan.id, TODAY) is True
    assert crud.create_notification(db, user.id, "overdue", loan.id, TODAY) is False
    assert crud.create_notification(db, user.id, "overdue", loan.id, date(2026, 9, 27)) is True
    assert _count_notifications(db) == 2


def test_create_notifications_deduplicates_recipients(db):
    first = _create_user(db, "user1")
    second = _create_user(db, "user2")
    equipment = _create_equipment(db)
    loan = _create_loan(db, first, equipment, date(2026, 10, 1), date(2026, 10, 5), "requested")
    services.create_notifications(db, [first.id, second.id, first.id], "new_request", loan.id, TODAY)
    assert _count_notifications(db) == 2


def test_health_check_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_token_returns_401(client):
    response = client.get("/api/_test_protected")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_valid_token_returns_user(client, db):
    user = _create_user(db)
    token = auth.create_access_token(user.id, user.token_generation, get_now())
    response = client.get("/api/_test_protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"id": user.id}


def test_generation_mismatch_inactive_and_unknown_user_return_same_401(client, db):
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
