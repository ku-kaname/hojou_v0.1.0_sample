"""
結合テスト：認証・ユーザー管理（ログイン・ログアウト・自分の情報取得・パスワード変更・ユーザー管理・初期管理者作成）

設計書：設計書/サーバー処理（main）/認証・ユーザー管理/、設計書/CRUD/認証・ユーザー管理/
"""

from contextlib import nullcontext
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth, crud, main, services
from app.database import get_db
from app.main import app
from app.models import Equipment, LoanRequest, LoanStatus, Notification, NotificationType, User
from app.schemas import AuthenticatedUser, PasswordChangeRequest
from app.services import get_now, get_today

PASSWORD = "Password123"


@pytest.fixture()
def client(db):
    """テスト用DBセッションを使うAPIクライアント"""
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_user(db, login_id, role="general", must_change=False, is_active=True, name="山田") -> User:
    user = User(
        login_id=login_id,
        name=name,
        password_hash=auth.hash_password(PASSWORD),
        role=role,
        must_change_password=must_change,
        is_active=is_active,
    )
    db.add(user)
    db.flush()
    return user


def _headers(user) -> dict[str, str]:
    token = auth.create_access_token(user.id, user.token_generation, get_now())
    return {"Authorization": f"Bearer {token}"}


def _make_loan(db, user, status) -> LoanRequest:
    equipment = Equipment(asset_number=f"A-{user.id}-{status}", name="PC", category="PC")
    db.add(equipment)
    db.flush()
    today = get_today()
    loan = LoanRequest(
        equipment_id=equipment.id,
        requester_id=user.id,
        start_date=today + timedelta(days=1),
        due_date=today + timedelta(days=2),
        purpose="test",
        status=status,
        requested_at=get_now(),
    )
    db.add(loan)
    db.flush()
    return loan


def _login(client, login_id, password=PASSWORD):
    return client.post("/api/auth/login", json={"login_id": login_id, "password": password})


# ---- ログイン ----


def test_login_success_returns_token_and_resets_failures(db, client):
    user = _make_user(db, "taro", must_change=True)
    user.failed_login_count = 3
    db.flush()
    response = _login(client, "taro")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["must_change_password"] is True
    assert user.failed_login_count == 0
    me = client.get("/api/users/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["login_id"] == "taro"


def test_login_failures_are_identical_401(db, client):
    _make_user(db, "taro")
    _make_user(db, "inactive", is_active=False)
    wrong_password = _login(client, "taro", "WrongPassword1")
    unknown_user = _login(client, "nobody")
    inactive_user = _login(client, "inactive")
    for response in (wrong_password, unknown_user, inactive_user):
        assert response.status_code == 401
        assert response.json() == {"detail": "ユーザーIDまたはパスワードが正しくありません"}
        assert response.headers["www-authenticate"] == "Bearer"


def test_login_locks_after_five_failures_and_unlocks_after_expiry(db, client):
    user = _make_user(db, "taro")
    for _ in range(5):
        assert _login(client, "taro", "WrongPassword1").status_code == 401
    assert user.failed_login_count == 5
    assert user.locked_until is not None
    # ロック中は正しいパスワードでも失敗し、失敗回数は加算されない
    assert _login(client, "taro").status_code == 401
    assert user.failed_login_count == 5
    # ロック期間が経過すると再びログインできる
    user.locked_until = get_now() - timedelta(seconds=1)
    db.flush()
    assert _login(client, "taro").status_code == 200
    assert user.failed_login_count == 0
    assert user.locked_until is None


def test_login_validation_error_422(client):
    assert client.post("/api/auth/login", json={"login_id": "ab", "password": "x"}).status_code == 422
    assert client.post("/api/auth/login", json={"login_id": "bad id!", "password": "x"}).status_code == 422
    assert client.post("/api/auth/login", json={"login_id": "taro", "password": ""}).status_code == 422


def test_login_does_not_expose_secrets_in_logs(db, client, caplog):
    _make_user(db, "taro")
    with caplog.at_level("INFO", logger="app"):
        _login(client, "taro", "SecretWrong123")
    assert "SecretWrong123" not in caplog.text
    assert "ログイン失敗" in caplog.text


# ---- ログアウト・自分の情報 ----


def test_logout_revokes_tokens(db, client):
    user = _make_user(db, "taro")
    headers = _headers(user)
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/users/me", headers=headers).status_code == 401
    assert client.post("/api/auth/logout").status_code == 401


def test_get_me_returns_public_fields_only(db, client):
    user = _make_user(db, "taro", must_change=True)
    response = client.get("/api/users/me", headers=_headers(user))
    assert response.status_code == 200
    assert set(response.json()) == {"id", "login_id", "name", "department", "role", "must_change_password"}


# ---- パスワード変更 ----


def test_change_password_success_issues_new_token(db, client):
    user = _make_user(db, "taro", must_change=True)
    old_headers = _headers(user)
    response = client.put(
        "/api/users/me/password",
        json={"current_password": PASSWORD, "new_password": "NewPassword456"},
        headers=old_headers,
    )
    assert response.status_code == 200
    assert response.json()["must_change_password"] is False
    assert user.must_change_password is False
    assert client.get("/api/users/me", headers=old_headers).status_code == 401
    new_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert client.get("/api/users/me", headers=new_headers).status_code == 200
    assert _login(client, "taro", "NewPassword456").status_code == 200


def test_change_password_rejects_wrong_current_and_locks(db, client):
    user = _make_user(db, "taro")
    headers = _headers(user)
    payload = {"current_password": "WrongPassword1", "new_password": "NewPassword456"}
    for _ in range(5):
        response = client.put("/api/users/me/password", json=payload, headers=headers)
        assert response.status_code == 400
        assert response.json() == {"detail": "現在のパスワードが正しくありません"}
    assert user.failed_login_count == 5
    assert user.locked_until is not None
    # ロック中は正しい現在のパスワードでも変更できず、失敗回数は加算されない
    locked = client.put(
        "/api/users/me/password",
        json={"current_password": PASSWORD, "new_password": "NewPassword456"},
        headers=headers,
    )
    assert locked.status_code == 400
    assert user.failed_login_count == 5


def test_change_password_rejects_same_password(db, client):
    user = _make_user(db, "taro")
    response = client.put(
        "/api/users/me/password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers=_headers(user),
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "新しいパスワードは現在のパスワードと異なる必要があります"}


def test_change_password_validation_error_422(db, client):
    user = _make_user(db, "taro")
    response = client.put(
        "/api/users/me/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=_headers(user),
    )
    assert response.status_code == 422


def test_must_change_password_blocks_admin_endpoints(db, client):
    admin = _make_user(db, "admin1", role="admin", must_change=True)
    response = client.get("/api/admin/users", headers=_headers(admin))
    assert response.status_code == 403
    assert response.json() == {"detail": "初期パスワードの変更が必要です"}


# ---- ユーザー登録・取得・一覧 ----


def test_admin_endpoints_require_admin_role(db, client):
    general = _make_user(db, "taro")
    headers = _headers(general)
    assert client.get("/api/admin/users", headers=headers).status_code == 403
    assert client.get("/api/admin/users/1", headers=headers).status_code == 403
    assert client.get("/api/admin/users").status_code == 401


def test_register_user_success_and_first_login_requires_change(db, client):
    admin = _make_user(db, "admin1", role="admin")
    body = {"login_id": "newuser", "name": "  新人  ", "role": "general", "initial_password": "InitPass1234"}
    response = client.post("/api/admin/users", json=body, headers=_headers(admin))
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "新人"
    assert created["department"] == ""
    assert created["must_change_password"] is True
    assert created["is_active"] is True
    assert created["created_at"].endswith("+09:00")
    assert "password_hash" not in created
    login = _login(client, "newuser", "InitPass1234")
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True


def test_register_user_duplicate_login_id_409_including_inactive(db, client):
    admin = _make_user(db, "admin1", role="admin")
    _make_user(db, "gone", is_active=False)
    for login_id in ("admin1", "gone"):
        body = {"login_id": login_id, "name": "x", "role": "general", "initial_password": "InitPass1234"}
        response = client.post("/api/admin/users", json=body, headers=_headers(admin))
        assert response.status_code == 409
        assert response.json() == {"detail": "このユーザーIDは既に登録されています"}


def test_register_user_concurrent_duplicate_login_id_409(db, client, monkeypatch):
    admin = _make_user(db, "admin1", role="admin")
    headers = _headers(admin)
    # 事前確認をすり抜けた同時登録を再現するため、重複確認が「なし」を返すようにする
    monkeypatch.setattr(crud, "get_user_by_login_id", lambda _db, _login_id, _for_update: None)
    body = {"login_id": "admin1", "name": "x", "role": "general", "initial_password": "InitPass1234"}
    response = client.post("/api/admin/users", json=body, headers=headers)
    assert response.status_code == 409
    assert response.json() == {"detail": "このユーザーIDは既に登録されています"}


def test_register_user_validation_error_422(db, client):
    admin = _make_user(db, "admin1", role="admin")
    headers = _headers(admin)
    base = {"login_id": "newuser", "name": "x", "role": "general", "initial_password": "InitPass1234"}
    for override in (
        {"role": "superuser"},
        {"login_id": "bad id"},
        {"initial_password": "short"},
        {"name": "   "},
    ):
        response = client.post("/api/admin/users", json={**base, **override}, headers=headers)
        assert response.status_code == 422


def test_list_users_filters_and_paging(db, client):
    admin = _make_user(db, "admin1", role="admin", name="管理")
    _make_user(db, "taro_1", name="太郎")
    _make_user(db, "hanako", name="花子", is_active=False)
    _make_user(db, "symbol", name="100%達成")
    headers = _headers(admin)

    everyone = client.get("/api/admin/users", headers=headers).json()
    assert everyone["total"] == 4
    assert [item["id"] for item in everyone["items"]] == sorted(item["id"] for item in everyone["items"])
    assert client.get("/api/admin/users?role=admin", headers=headers).json()["total"] == 1
    assert client.get("/api/admin/users?is_active=false", headers=headers).json()["total"] == 1
    assert client.get("/api/admin/users?keyword=花子", headers=headers).json()["total"] == 1
    assert client.get("/api/admin/users?keyword=TARO", headers=headers).json()["total"] == 1
    # LIKEのワイルドカードは文字として扱う
    assert client.get("/api/admin/users?keyword=%25", headers=headers).json()["total"] == 1
    assert client.get("/api/admin/users?keyword=_", headers=headers).json()["total"] == 1
    paged = client.get("/api/admin/users?page=2&page_size=3", headers=headers).json()
    assert (paged["page"], paged["page_size"], len(paged["items"]), paged["total"]) == (2, 3, 1, 4)
    assert client.get("/api/admin/users?page=0", headers=headers).status_code == 422
    assert client.get("/api/admin/users?role=x", headers=headers).status_code == 422


def test_get_user_returns_user_or_404(db, client):
    admin = _make_user(db, "admin1", role="admin")
    headers = _headers(admin)
    assert client.get(f"/api/admin/users/{admin.id}", headers=headers).json()["login_id"] == "admin1"
    missing = client.get("/api/admin/users/999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json() == {"detail": "ユーザーが見つかりません"}
    assert client.get("/api/admin/users/0", headers=headers).status_code == 422
    assert client.get("/api/admin/users/abc", headers=headers).status_code == 422


# ---- ユーザー編集 ----


def _update_body(user, **override):
    body = {"name": user.name, "department": user.department, "role": user.role, "is_active": user.is_active}
    body.update(override)
    return body


def test_update_user_changes_profile_without_revoking_token(db, client):
    admin = _make_user(db, "admin1", role="admin")
    target = _make_user(db, "taro")
    target_headers = _headers(target)
    response = client.put(
        f"/api/admin/users/{target.id}",
        json=_update_body(target, name="次郎", department="総務"),
        headers=_headers(admin),
    )
    assert response.status_code == 200
    assert (response.json()["name"], response.json()["department"]) == ("次郎", "総務")
    assert client.get("/api/users/me", headers=target_headers).status_code == 200


def test_update_user_role_change_revokes_tokens(db, client):
    admin = _make_user(db, "admin1", role="admin")
    target = _make_user(db, "taro")
    target_headers = _headers(target)
    response = client.put(
        f"/api/admin/users/{target.id}", json=_update_body(target, role="admin"), headers=_headers(admin)
    )
    assert response.status_code == 200
    assert client.get("/api/users/me", headers=target_headers).status_code == 401


def test_update_user_404_and_422(db, client):
    admin = _make_user(db, "admin1", role="admin")
    headers = _headers(admin)
    response = client.put("/api/admin/users/999999", json=_update_body(admin), headers=headers)
    assert response.status_code == 404
    assert client.put(f"/api/admin/users/{admin.id}", json={"name": "x"}, headers=headers).status_code == 422


def test_update_user_cannot_remove_last_active_admin(db, client):
    admin = _make_user(db, "admin1", role="admin")
    headers = _headers(admin)
    expected = {"detail": "最後の有効な管理者は無効化・ロール変更できません"}
    deactivate = client.put(f"/api/admin/users/{admin.id}", json=_update_body(admin, is_active=False), headers=headers)
    demote = client.put(f"/api/admin/users/{admin.id}", json=_update_body(admin, role="general"), headers=headers)
    assert (deactivate.status_code, deactivate.json()) == (400, expected)
    assert (demote.status_code, demote.json()) == (400, expected)


def test_update_user_allows_self_demotion_when_other_admin_exists(db, client):
    admin = _make_user(db, "admin1", role="admin")
    _make_user(db, "admin2", role="admin")
    response = client.put(
        f"/api/admin/users/{admin.id}", json=_update_body(admin, role="general"), headers=_headers(admin)
    )
    assert response.status_code == 200
    assert response.json()["role"] == "general"


def test_update_user_deactivation_rejected_when_loan_is_lent(db, client):
    admin = _make_user(db, "admin1", role="admin")
    target = _make_user(db, "taro")
    _make_loan(db, target, LoanStatus.LENT.value)
    response = client.put(
        f"/api/admin/users/{target.id}", json=_update_body(target, is_active=False), headers=_headers(admin)
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "貸出中の申請があるユーザーは無効化できません"}
    assert target.is_active is True


def test_update_user_deactivation_cancels_pending_loans_and_notifies(db, client):
    admin = _make_user(db, "admin1", role="admin")
    target = _make_user(db, "taro")
    target_headers = _headers(target)
    requested = _make_loan(db, target, LoanStatus.REQUESTED.value)
    approved = _make_loan(db, target, LoanStatus.APPROVED.value)
    returned = _make_loan(db, target, LoanStatus.RETURNED.value)
    response = client.put(
        f"/api/admin/users/{target.id}", json=_update_body(target, is_active=False), headers=_headers(admin)
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    for loan in (requested, approved):
        assert loan.status == LoanStatus.CANCELED.value
        assert loan.canceled_by is None
        assert loan.canceled_at is not None
        assert loan.reason == "ユーザー無効化に伴う自動取消"
    assert returned.status == LoanStatus.RETURNED.value
    notifications = db.scalars(select(Notification).where(Notification.recipient_id == target.id)).all()
    assert sorted(n.loan_request_id for n in notifications) == sorted([requested.id, approved.id])
    assert {n.type for n in notifications} == {NotificationType.CANCELED.value}
    assert client.get("/api/users/me", headers=target_headers).status_code == 401
    assert _login(client, "taro").status_code == 401


def test_update_user_reactivation_clears_lock(db, client):
    admin = _make_user(db, "admin1", role="admin")
    target = _make_user(db, "taro", is_active=False)
    target.failed_login_count = 5
    target.locked_until = get_now() + timedelta(minutes=10)
    db.flush()
    response = client.put(
        f"/api/admin/users/{target.id}", json=_update_body(target, is_active=True), headers=_headers(admin)
    )
    assert response.status_code == 200
    assert (target.failed_login_count, target.locked_until) == (0, None)
    assert _login(client, "taro").status_code == 200


# ---- パスワード初期化 ----


def test_reset_password_forces_change_and_revokes_tokens(db, client):
    admin = _make_user(db, "admin1", role="admin")
    target = _make_user(db, "taro")
    target.failed_login_count = 5
    target.locked_until = get_now() + timedelta(minutes=10)
    db.flush()
    target_headers = _headers(target)
    response = client.post(
        f"/api/admin/users/{target.id}/reset-password", json={"new_password": "ResetPass123"}, headers=_headers(admin)
    )
    assert response.status_code == 204
    assert response.content == b""
    assert target.must_change_password is True
    assert (target.failed_login_count, target.locked_until) == (0, None)
    assert client.get("/api/users/me", headers=target_headers).status_code == 401
    login = _login(client, "taro", "ResetPass123")
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True


def test_reset_password_404_and_422(db, client):
    admin = _make_user(db, "admin1", role="admin")
    headers = _headers(admin)
    missing = client.post(
        "/api/admin/users/999999/reset-password", json={"new_password": "ResetPass123"}, headers=headers
    )
    assert missing.status_code == 404
    short = client.post(f"/api/admin/users/{admin.id}/reset-password", json={"new_password": "short"}, headers=headers)
    assert short.status_code == 422


# ---- CRUD ----


def test_crud_get_user_by_login_id_is_case_sensitive(db):
    user = _make_user(db, "Taro")
    assert crud.get_user_by_login_id(db, "Taro") is user
    assert crud.get_user_by_login_id(db, "taro") is None
    assert crud.get_user_by_login_id(db, "Taro", True) is user


def test_crud_active_admin_ids_are_ordered_and_exclude_inactive(db):
    first = _make_user(db, "admin1", role="admin")
    _make_user(db, "admin_off", role="admin", is_active=False)
    _make_user(db, "general1")
    second = _make_user(db, "admin2", role="admin")
    assert crud.get_active_admin_ids(db) == [first.id, second.id]
    assert crud.lock_active_admin_ids(db) == [first.id, second.id]


def test_crud_update_password_increments_generation(db):
    user = _make_user(db, "taro")
    user.failed_login_count = 3
    now = get_now()
    crud.update_password(db, user, "new-hash", True, now)
    assert (user.token_generation, user.failed_login_count, user.locked_until, user.must_change_password) == (
        1,
        0,
        None,
        True,
    )
    assert user.password_hash == "new-hash"
    crud.increment_token_generation(db, user, now)
    assert user.token_generation == 2


# ---- 初期管理者作成 ----


def test_ensure_initial_admin_creates_admin_once(db, monkeypatch, caplog):
    monkeypatch.setenv("INITIAL_ADMIN_LOGIN_ID", "first_admin")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "InitialAdmin123")
    with caplog.at_level("INFO", logger="app"):
        services.ensure_initial_admin(db)
        services.ensure_initial_admin(db)
    admins = db.scalars(select(User).where(User.role == "admin")).all()
    assert [a.login_id for a in admins] == ["first_admin"]
    assert admins[0].must_change_password is True
    assert auth.verify_password("InitialAdmin123", admins[0].password_hash)
    assert "InitialAdmin123" not in caplog.text
    assert "first_admin" in caplog.text


def test_ensure_initial_admin_does_nothing_when_admin_exists(db, monkeypatch):
    _make_user(db, "admin1", role="admin")
    monkeypatch.delenv("INITIAL_ADMIN_LOGIN_ID", raising=False)
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    services.ensure_initial_admin(db)
    assert db.scalars(select(User)).all() == [crud.get_user_by_login_id(db, "admin1")]


@pytest.mark.parametrize(
    ("login_id", "password"),
    [
        ("", "InitialAdmin123"),
        ("first_admin", ""),
        ("ab", "InitialAdmin123"),
        ("bad id", "InitialAdmin123"),
        ("first_admin", "short"),
    ],
)
def test_ensure_initial_admin_rejects_invalid_environment(db, monkeypatch, login_id, password):
    monkeypatch.setenv("INITIAL_ADMIN_LOGIN_ID", login_id)
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", password)
    with pytest.raises(RuntimeError, match="初期管理者の環境変数が未設定または不正です") as error:
        services.ensure_initial_admin(db)
    assert password not in str(error.value) or password == ""


def test_ensure_initial_admin_rejects_duplicate_login_id(db, monkeypatch):
    _make_user(db, "first_admin", is_active=False)
    monkeypatch.setenv("INITIAL_ADMIN_LOGIN_ID", "first_admin")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "InitialAdmin123")
    with pytest.raises(RuntimeError, match="既存ユーザーと重複"):
        services.ensure_initial_admin(db)


# ---- ユーザー不在の401・起動時処理 ----


def test_services_return_401_when_authenticated_user_is_missing(db):
    ghost = AuthenticatedUser(id=999999, login_id="ghost", name="x", role="general", must_change_password=False)
    change_request = PasswordChangeRequest(current_password=PASSWORD, new_password="NewPassword456")
    for call in (
        lambda: services.get_my_profile(db, ghost),
        lambda: services.logout_user(db, ghost),
        lambda: services.change_own_password(db, ghost, change_request),
    ):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 401
        assert error.value.detail == "資格情報を検証できませんでした"


def test_lifespan_creates_initial_admin(db, monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_LOGIN_ID", "first_admin")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "InitialAdmin123")
    monkeypatch.setattr(main, "get_session_factory", lambda: lambda: nullcontext(db))
    with TestClient(app):
        pass
    assert crud.get_user_by_login_id(db, "first_admin") is not None


def test_lifespan_aborts_startup_when_environment_is_invalid(db, monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_LOGIN_ID", raising=False)
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(main, "get_session_factory", lambda: lambda: nullcontext(db))
    with pytest.raises(RuntimeError, match="初期管理者の環境変数が未設定または不正です"), TestClient(app):
        pass
