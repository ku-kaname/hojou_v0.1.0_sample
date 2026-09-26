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
    """
    【結合テスト項番1】正常系 - ログイン成功でトークンを発行し、失敗回数を0に戻す
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro・初期パスワード変更要・失敗回数3）が登録済み
    入力値：POST /api/auth/login {"login_id": "taro", "password": "正しいパスワード"}
    想定結果：
    ・HTTP 200
    ・token_type が bearer、must_change_password が true
    ・DBの失敗回数が0
    ・返却されたトークンで GET /api/users/me が200になり login_id が taro
    """
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
    """
    【結合テスト項番2】異常系 - パスワード誤り・存在しないユーザー・無効化ユーザーは同一の401を返す
    前提条件：
    ・テスト用DB起動済み
    ・有効ユーザー（taro）と無効化ユーザー（inactive）が登録済み
    入力値：3通りを送信：taroに誤ったパスワード／存在しないログインID／inactiveに正しいパスワード
    想定結果：
    ・3件とも HTTP 401
    ・detail が "ユーザーIDまたはパスワードが正しくありません"（3件とも同一で、原因を推測できない）
    ・WWW-Authenticate ヘッダーが Bearer
    """
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
    """
    【結合テスト項番3】異常系・境界値 - 5回連続失敗でロックし、ロック中は加算されず、
      期間経過後に解除される
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro）が登録済み
    入力値：
    ・誤ったパスワードで5回ログイン
    ・ロック中に正しいパスワードでログイン
    ・ロック解除日時を過去にして正しいパスワードでログイン
    想定結果：
    ・5回とも HTTP 401、失敗回数5・ロック解除日時が設定される
    ・ロック中は正しいパスワードでも HTTP 401、失敗回数は5のまま
    ・解除後は HTTP 200、失敗回数0・ロック解除日時なし
    """
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
    """
    【結合テスト項番4】異常系 - 入力チェックエラー（ログインIDが短い・使用不可の文字・パスワードが空
      ）
    前提条件：
    ・テスト用DB起動済み
    入力値：
    ・login_id が2文字（ab）
    ・login_id に空白・記号を含む（bad id!）
    ・password が空文字
    想定結果：3件とも HTTP 422
    """
    assert client.post("/api/auth/login", json={"login_id": "ab", "password": "x"}).status_code == 422
    assert client.post("/api/auth/login", json={"login_id": "bad id!", "password": "x"}).status_code == 422
    assert client.post("/api/auth/login", json={"login_id": "taro", "password": ""}).status_code == 422


def test_login_does_not_expose_secrets_in_logs(db, client, caplog):
    """
    【結合テスト項番5】セキュリティ - ログイン失敗をログに記録するが、入力したパスワードは記録しない
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro）が登録済み
    入力値：taro に誤ったパスワード（SecretWrong123）でログイン
    想定結果：
    ・ログインに「ログイン失敗」と記録される
    ・ログの文字列に入力したパスワードが含まれない
    """
    _make_user(db, "taro")
    with caplog.at_level("INFO", logger="app"):
        _login(client, "taro", "SecretWrong123")
    assert "SecretWrong123" not in caplog.text
    assert "ログイン失敗" in caplog.text


# ---- ログアウト・自分の情報 ----


def test_logout_revokes_tokens(db, client):
    """
    【結合テスト項番6】正常系・異常系 - ログアウトで発行済みトークンが失効する。トークンなしは401
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro）が登録済み・トークン取得済み
    入力値：
    ・POST /api/auth/logout（Bearer 認証あり）
    ・同じトークンで GET /api/users/me
    ・トークンなしで POST /api/auth/logout
    想定結果：
    ・HTTP 204
    ・失効したトークンでは HTTP 401
    ・トークンなしは HTTP 401
    """
    user = _make_user(db, "taro")
    headers = _headers(user)
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/users/me", headers=headers).status_code == 401
    assert client.post("/api/auth/logout").status_code == 401


def test_get_me_returns_public_fields_only(db, client):
    """
    【結合テスト項番7】正常系 - 画面表示に必要な項目だけを返す（パスワード関連の項目を返さない）
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro・初期パスワード変更要）が登録済み・トークン取得済み
    入力値：GET /api/users/me（Bearer 認証あり）
    想定結果：
    ・HTTP 200
    ・返却項目が id・login_id・name・department・role・must_change_password のみ
    """
    user = _make_user(db, "taro", must_change=True)
    response = client.get("/api/users/me", headers=_headers(user))
    assert response.status_code == 200
    assert set(response.json()) == {"id", "login_id", "name", "department", "role", "must_change_password"}


# ---- パスワード変更 ----


def test_change_password_success_issues_new_token(db, client):
    """
    【結合テスト項番8】正常系 - パスワード変更で新しいトークンを発行し、旧トークンを失効する
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro・初期パスワード変更要）が登録済み・トークン取得済み
    入力値：PUT /api/users/me/password {"current_password": "現在のパスワード", "new_password": "New
      Password456"}
    想定結果：
    ・HTTP 200、must_change_password が false、DBの初期パスワード変更要が false
    ・旧トークンでは GET /api/users/me が HTTP 401
    ・新トークンでは HTTP 200
    ・新しいパスワードでログイン可能（HTTP 200）
    """
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
    """
    【結合テスト項番9】異常系・境界値 - 現在のパスワード誤りを5回繰り返すとロックし、
      ロック中は加算されない
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro）が登録済み・トークン取得済み
    入力値：
    ・current_password に誤った値で5回送信
    ・ロック中に正しい current_password で送信
    想定結果：
    ・5回とも HTTP 400、detail が「現在のパスワードが正しくありません」
    ・失敗回数5・ロック解除日時が設定される
    ・ロック中は正しい値でも HTTP 400、失敗回数は5のまま
    """
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
    """
    【結合テスト項番10】異常系 - 新しいパスワードが現在と同じ
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro）が登録済み・トークン取得済み
    入力値：current_password と new_password に同じ値を指定
    想定結果：
    ・HTTP 400
    ・detail が「新しいパスワードは現在のパスワードと異なる必要があります」
    """
    user = _make_user(db, "taro")
    response = client.put(
        "/api/users/me/password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers=_headers(user),
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "新しいパスワードは現在のパスワードと異なる必要があります"}


def test_change_password_validation_error_422(db, client):
    """
    【結合テスト項番11】異常系 - 入力チェックエラー（新しいパスワードが8文字未満）
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（taro）が登録済み・トークン取得済み
    入力値：new_password が5文字（short）
    想定結果：HTTP 422
    """
    user = _make_user(db, "taro")
    response = client.put(
        "/api/users/me/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=_headers(user),
    )
    assert response.status_code == 422


def test_must_change_password_blocks_admin_endpoints(db, client):
    """
    【結合テスト項番13】異常系 - 初期パスワード未変更の管理者は管理者機能を使えない
    前提条件：
    ・テスト用DB起動済み
    ・管理者（初期パスワード変更要）が登録済み・トークン取得済み
    入力値：GET /api/admin/users（Bearer 認証あり）
    想定結果：
    ・HTTP 403
    ・detail が "初期パスワードの変更が必要です"
    """
    admin = _make_user(db, "admin1", role="admin", must_change=True)
    response = client.get("/api/admin/users", headers=_headers(admin))
    assert response.status_code == 403
    assert response.json() == {"detail": "初期パスワードの変更が必要です"}


# ---- ユーザー登録・取得・一覧 ----


def test_admin_endpoints_require_admin_role(db, client):
    """
    【結合テスト項番12】異常系 - 認証なしは401、一般ユーザーは403
    前提条件：
    ・テスト用DB起動済み
    ・一般ユーザー（taro）が登録済み・トークン取得済み
    入力値：
    ・一般ユーザーのトークンで GET /api/admin/users と GET /api/admin/users/1
    ・トークンなしで GET /api/admin/users
    想定結果：
    ・一般ユーザーは HTTP 403
    ・トークンなしは HTTP 401
    """
    general = _make_user(db, "taro")
    headers = _headers(general)
    assert client.get("/api/admin/users", headers=headers).status_code == 403
    assert client.get("/api/admin/users/1", headers=headers).status_code == 403
    assert client.get("/api/admin/users").status_code == 401


def test_register_user_success_and_first_login_requires_change(db, client):
    """
    【結合テスト項番15】正常系 - ユーザーを登録し、初回ログインでパスワード変更が必要になる
    前提条件：
    ・テスト用DB起動済み
    ・管理者が登録済み・トークン取得済み
    入力値：POST /api/admin/users {"login_id": "newuser", "name": "  新人  ", "role": "general", "in
      itial_password": "InitPass1234"}
    想定結果：
    ・HTTP 201
    ・name が前後の空白を除いた「新人」、department が空文字、must_change_password が true、
        is_active が true
    ・created_at が +09:00 の日時
    ・password_hash を返さない
    ・初期パスワードでログインでき、must_change_password が true
    """
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
    """
    【結合テスト項番16】異常系 - ログインIDが重複（無効化済みユーザーのIDも重複扱い）
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin1）と無効化済みユーザー（gone）が登録済み
    入力値：login_id に admin1・gone をそれぞれ指定して登録
    想定結果：
    ・2件とも HTTP 409
    ・detail が "このユーザーIDは既に登録されています"
    """
    admin = _make_user(db, "admin1", role="admin")
    _make_user(db, "gone", is_active=False)
    for login_id in ("admin1", "gone"):
        body = {"login_id": login_id, "name": "x", "role": "general", "initial_password": "InitPass1234"}
        response = client.post("/api/admin/users", json=body, headers=_headers(admin))
        assert response.status_code == 409
        assert response.json() == {"detail": "このユーザーIDは既に登録されています"}


def test_register_user_concurrent_duplicate_login_id_409(db, client, monkeypatch):
    """
    【結合テスト項番17】異常系 - 同時登録で事前確認をすり抜けた重複もDBの一意制約で409にする
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin1）が登録済み。事前の重複確認が「なし」を返すよう差し替える
    入力値：login_id に登録済みの admin1 を指定して登録
    想定結果：
    ・HTTP 409
    ・detail が "このユーザーIDは既に登録されています"
    """
    admin = _make_user(db, "admin1", role="admin")
    headers = _headers(admin)
    # 事前確認をすり抜けた同時登録を再現するため、重複確認が「なし」を返すようにする
    monkeypatch.setattr(crud, "get_user_by_login_id", lambda _db, _login_id, _for_update: None)
    body = {"login_id": "admin1", "name": "x", "role": "general", "initial_password": "InitPass1234"}
    response = client.post("/api/admin/users", json=body, headers=headers)
    assert response.status_code == 409
    assert response.json() == {"detail": "このユーザーIDは既に登録されています"}


def test_register_user_validation_error_422(db, client):
    """
    【結合テスト項番18】異常系 - 入力チェックエラー（ロール不正・ログインID不正・初期パスワードが短
      い・氏名が空白のみ）
    前提条件：
    ・テスト用DB起動済み
    ・管理者が登録済み・トークン取得済み
    入力値：
    ・role=superuser
    ・login_id=bad id
    ・initial_password=short
    ・name=空白のみ
    想定結果：4件とも HTTP 422
    """
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
    """
    【結合テスト項番14】正常系 - 絞り込み（ロール・有効フラグ・キーワード）とページングができる。
      入力チェックエラーは422
    前提条件：
    ・テスト用DB起動済み
    ・管理者・有効ユーザー（太郎）・無効ユーザー（花子）
        ・氏名に「%」を含むユーザーの計4件が登録済み（絞り込み対象外のデータが存在する）
    入力値：
    ・クエリなし
    ・role=admin
    ・is_active=false
    ・keyword=花子・TARO（大文字小文字を区別しない）
    ・keyword=%25（記号の%）・keyword=_
    ・page=2&page_size=3
    ・page=0・role=x
    想定結果：
    ・全件（total=4）がID昇順で返る
    ・各絞り込み条件で該当件数（1件）だけ返る
    ・LIKEのワイルドカード（%・_）は文字として扱われ、該当する1件だけ返る
    ・page=2・page_size=3 で1件・total=4
    ・page=0・role=x は HTTP 422
    """
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
    """
    【結合テスト項番19】正常系・異常系 - ユーザーを取得できる。存在しないIDは404、ID形式不正は422
    前提条件：
    ・テスト用DB起動済み
    ・管理者（admin1）が登録済み・トークン取得済み
    入力値：
    ・管理者自身のID
    ・999999（存在しない）
    ・0・abc
    想定結果：
    ・HTTP 200、login_id が admin1
    ・HTTP 404、detail が "ユーザーが見つかりません"
    ・HTTP 422
    """
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
    """
    【結合テスト項番20】正常系 - 氏名・所属の変更ではトークンを失効させない
    前提条件：
    ・テスト用DB起動済み
    ・管理者と一般ユーザー（taro）が登録済み・taroのトークン取得済み
    入力値：PUT /api/admin/users/{taroのID} name=次郎・department=総務
    想定結果：
    ・HTTP 200、name が次郎・department が総務
    ・taro の既存トークンで GET /api/users/me が HTTP 200
    """
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
    """
    【結合テスト項番21】正常系 - ロール変更で対象ユーザーのトークンを失効させる
    前提条件：
    ・テスト用DB起動済み
    ・管理者と一般ユーザー（taro）が登録済み・taroのトークン取得済み
    入力値：PUT /api/admin/users/{taroのID} role=admin
    想定結果：
    ・HTTP 200
    ・taro の既存トークンで GET /api/users/me が HTTP 401
    """
    admin = _make_user(db, "admin1", role="admin")
    target = _make_user(db, "taro")
    target_headers = _headers(target)
    response = client.put(
        f"/api/admin/users/{target.id}", json=_update_body(target, role="admin"), headers=_headers(admin)
    )
    assert response.status_code == 200
    assert client.get("/api/users/me", headers=target_headers).status_code == 401


def test_update_user_404_and_422(db, client):
    """
    【結合テスト項番22】異常系 - 存在しないユーザーは404、必須項目の欠落は422
    前提条件：
    ・テスト用DB起動済み
    ・管理者が登録済み・トークン取得済み
    入力値：
    ・PUT /api/admin/users/999999（正しい本文）
    ・PUT /api/admin/users/{管理者のID} 本文が name のみ
    想定結果：
    ・HTTP 404
    ・HTTP 422
    """
    admin = _make_user(db, "admin1", role="admin")
    headers = _headers(admin)
    response = client.put("/api/admin/users/999999", json=_update_body(admin), headers=headers)
    assert response.status_code == 404
    assert client.put(f"/api/admin/users/{admin.id}", json={"name": "x"}, headers=headers).status_code == 422


def test_update_user_cannot_remove_last_active_admin(db, client):
    """
    【結合テスト項番23】異常系 - 最後の有効な管理者は無効化・ロール変更できない
    前提条件：
    ・テスト用DB起動済み
    ・有効な管理者が1人だけ登録済み
    入力値：
    ・自分自身を is_active=false にする
    ・自分自身を role=general にする
    想定結果：
    ・2件とも HTTP 400
    ・detail が "最後の有効な管理者は無効化・ロール変更できません"
    """
    admin = _make_user(db, "admin1", role="admin")
    headers = _headers(admin)
    expected = {"detail": "最後の有効な管理者は無効化・ロール変更できません"}
    deactivate = client.put(f"/api/admin/users/{admin.id}", json=_update_body(admin, is_active=False), headers=headers)
    demote = client.put(f"/api/admin/users/{admin.id}", json=_update_body(admin, role="general"), headers=headers)
    assert (deactivate.status_code, deactivate.json()) == (400, expected)
    assert (demote.status_code, demote.json()) == (400, expected)


def test_update_user_allows_self_demotion_when_other_admin_exists(db, client):
    """
    【結合テスト項番24】正常系 - 他に有効な管理者がいれば自分のロールを変更できる
    前提条件：
    ・テスト用DB起動済み
    ・有効な管理者が2人登録済み
    入力値：管理者（admin1）が自分自身を role=general にする
    想定結果：
    ・HTTP 200
    ・role が general
    """
    admin = _make_user(db, "admin1", role="admin")
    _make_user(db, "admin2", role="admin")
    response = client.put(
        f"/api/admin/users/{admin.id}", json=_update_body(admin, role="general"), headers=_headers(admin)
    )
    assert response.status_code == 200
    assert response.json()["role"] == "general"


def test_update_user_deactivation_rejected_when_loan_is_lent(db, client):
    """
    【結合テスト項番25】異常系 - 貸出中の申請があるユーザーは無効化できない
    前提条件：
    ・テスト用DB起動済み
    ・管理者と一般ユーザー（taro）が登録済み。taro の申請（貸出中）が存在する
    入力値：PUT /api/admin/users/{taroのID} is_active=false
    想定結果：
    ・HTTP 400
    ・detail が "貸出中の申請があるユーザーは無効化できません"
    ・taro は有効なまま
    """
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
    """
    【結合テスト項番26】正常系 - 無効化で申請中・承認済みの申請を自動取消し、通知を作り、
      トークンを失効させる
    前提条件：
    ・テスト用DB起動済み
    ・管理者と一般ユーザー（taro）が登録済み。taro の申請が申請中・承認済み・返却済みで各1件（返却済
        みは取消対象外）
    入力値：PUT /api/admin/users/{taroのID} is_active=false
    想定結果：
    ・HTTP 200、is_active が false
    ・申請中・承認済みの申請が取消済みになり、
        取消者なし・取消日時あり・理由が「ユーザー無効化に伴う自動取消」
    ・返却済みの申請は変わらない
    ・取消した2件について taro宛の「取消」通知が作られる
    ・taro のトークンで HTTP 401、ログインも HTTP 401
    """
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
    """
    【結合テスト項番27】正常系 - 再有効化でロック状態を解除する
    前提条件：
    ・テスト用DB起動済み
    ・管理者と無効化済みユーザー（taro・失敗回数5・ロック中）が登録済み
    入力値：PUT /api/admin/users/{taroのID} is_active=true
    想定結果：
    ・HTTP 200
    ・失敗回数0・ロック解除日時なし
    ・taro がログインできる（HTTP 200）
    """
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
    """
    【結合テスト項番28】正常系 - パスワードを初期化し、次回ログインで変更を求め、
      既存トークンを失効させる
    前提条件：
    ・テスト用DB起動済み
    ・管理者と一般ユーザー（taro・失敗回数5・ロック中）が登録済み・taroのトークン取得済み
    入力値：POST /api/admin/users/{taroのID}/reset-password {"new_password": "ResetPass123"}
    想定結果：
    ・HTTP 204（本文なし）
    ・初期パスワード変更要が true、失敗回数0・ロック解除日時なし
    ・taro の既存トークンで HTTP 401
    ・新しいパスワードでログインでき must_change_password が true
    """
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
    """
    【結合テスト項番29】異常系 - 存在しないユーザーは404、新しいパスワードが短い場合は422
    前提条件：
    ・テスト用DB起動済み
    ・管理者が登録済み・トークン取得済み
    入力値：
    ・POST /api/admin/users/999999/reset-password
    ・new_password が5文字（short）
    想定結果：
    ・HTTP 404
    ・HTTP 422
    """
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
    """
    【結合テスト項番30】DBアクセス - ログインIDの検索は大文字小文字を区別する
    前提条件：
    ・テスト用DB起動済み
    ・ログインID「Taro」のユーザーが登録済み
    入力値：get_user_by_login_id に Taro・taro・（Taro, 行ロックあり）を指定
    想定結果：
    ・Taro は見つかる
    ・taro は見つからない（None）
    ・行ロック指定でも Taro が見つかる
    """
    user = _make_user(db, "Taro")
    assert crud.get_user_by_login_id(db, "Taro") is user
    assert crud.get_user_by_login_id(db, "taro") is None
    assert crud.get_user_by_login_id(db, "Taro", True) is user


def test_crud_active_admin_ids_are_ordered_and_exclude_inactive(db):
    """
    【結合テスト項番31】DBアクセス - 有効な管理者IDをID昇順で返し、
      無効化済み・一般ユーザーは含めない
    前提条件：
    ・テスト用DB起動済み
    ・管理者2人・無効化済み管理者1人・一般ユーザー1人が登録済み
    入力値：get_active_admin_ids と lock_active_admin_ids を呼ぶ
    想定結果：どちらも有効な管理者2人のIDがID昇順で返る
    """
    first = _make_user(db, "admin1", role="admin")
    _make_user(db, "admin_off", role="admin", is_active=False)
    _make_user(db, "general1")
    second = _make_user(db, "admin2", role="admin")
    assert crud.get_active_admin_ids(db) == [first.id, second.id]
    assert crud.lock_active_admin_ids(db) == [first.id, second.id]


def test_crud_update_password_increments_generation(db):
    """
    【結合テスト項番32】DBアクセス - パスワード更新でトークン世代を進め、ロック状態を解除する
    前提条件：
    ・テスト用DB起動済み
    ・ユーザー（失敗回数3）が登録済み
    入力値：update_password（初期パスワード変更要=true）→ increment_token_generation
    想定結果：
    ・トークン世代が1・失敗回数0・ロック解除日時なし・初期パスワード変更要が true
    ・パスワードのハッシュが更新される
    ・increment_token_generation 後は世代が2
    """
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
    """
    【結合テスト項番33】正常系 - 初期管理者を環境変数から1回だけ作成する（秘密情報をログに出さない）
    前提条件：
    ・テスト用DB起動済み
    ・有効な管理者が存在せず、環境変数（INITIAL_ADMIN_LOGIN_ID・INITIAL_ADMIN_PASSWORD）が設定済み
    入力値：ensure_initial_admin を2回続けて実行
    想定結果：
    ・管理者は first_admin の1人だけ
    ・初期パスワード変更要が true、パスワードはハッシュ化されて検証できる
    ・ログにパスワードが含まれず、ログインIDが含まれる
    """
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
    """
    【結合テスト項番34】正常系 - 管理者が既にいれば何もしない（環境変数が未設定でもエラーにしない）
    前提条件：
    ・テスト用DB起動済み
    ・管理者が登録済み。環境変数は未設定
    入力値：ensure_initial_admin を実行
    想定結果：ユーザーは既存の1人のまま増えず、エラーにならない
    """
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
    """
    【結合テスト項番35】異常系 - 環境変数が未設定・不正なら起動を止める（パラメーター化5件）
    前提条件：
    ・テスト用DB起動済み
    ・有効な管理者が存在しない
    入力値：
    ・ログインIDが空
    ・パスワードが空
    ・ログインIDが2文字
    ・ログインIDに空白を含む
    ・パスワードが5文字
    想定結果：
    ・5件とも RuntimeError（メッセージ「初期管理者の環境変数が未設定または不正です」）
    ・メッセージに入力したパスワードを含まない
    """
    monkeypatch.setenv("INITIAL_ADMIN_LOGIN_ID", login_id)
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", password)
    with pytest.raises(RuntimeError, match="初期管理者の環境変数が未設定または不正です") as error:
        services.ensure_initial_admin(db)
    assert password not in str(error.value) or password == ""


def test_ensure_initial_admin_rejects_duplicate_login_id(db, monkeypatch):
    """
    【結合テスト項番36】異常系 - 環境変数のログインIDが既存ユーザー（無効化済み）と重複
    前提条件：
    ・テスト用DB起動済み
    ・無効化済みユーザー（first_admin）が登録済み。環境変数のログインIDも first_admin
    入力値：ensure_initial_admin を実行
    想定結果：RuntimeError（メッセージに「既存ユーザーと重複」を含む）
    """
    _make_user(db, "first_admin", is_active=False)
    monkeypatch.setenv("INITIAL_ADMIN_LOGIN_ID", "first_admin")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "InitialAdmin123")
    with pytest.raises(RuntimeError, match="既存ユーザーと重複"):
        services.ensure_initial_admin(db)


# ---- ユーザー不在の401・起動時処理 ----


def test_services_return_401_when_authenticated_user_is_missing(db):
    """
    【結合テスト項番37】異常系 - トークンは有効でもユーザーがDBに存在しなければ401
    前提条件：
    ・テスト用DB起動済み
    ・認証済みとして渡すユーザー（ID 999999）がDBに存在しない
    入力値：get_my_profile・logout_user・change_own_password を呼ぶ
    想定結果：
    ・3件とも HTTP 401（例外）
    ・detail が "資格情報を検証できませんでした"
    """
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
    """
    【結合テスト項番38】正常系 - アプリ起動時に初期管理者を作成する
    前提条件：
    ・テスト用DB起動済み
    ・環境変数が設定済み。起動時のDB接続をテスト用DBに差し替える
    入力値：アプリを起動（TestClient のコンテキストに入る）
    想定結果：first_admin がDBに作成される
    """
    monkeypatch.setenv("INITIAL_ADMIN_LOGIN_ID", "first_admin")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "InitialAdmin123")
    monkeypatch.setattr(main, "get_session_factory", lambda: lambda: nullcontext(db))
    with TestClient(app):
        pass
    assert crud.get_user_by_login_id(db, "first_admin") is not None


def test_lifespan_aborts_startup_when_environment_is_invalid(db, monkeypatch):
    """
    【結合テスト項番39】異常系 - 環境変数が不正ならアプリの起動を中止する
    前提条件：
    ・テスト用DB起動済み
    ・有効な管理者が存在せず、環境変数は未設定
    入力値：アプリを起動
    想定結果：RuntimeError（「初期管理者の環境変数が未設定または不正です」）で起動に失敗する
    """
    monkeypatch.delenv("INITIAL_ADMIN_LOGIN_ID", raising=False)
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(main, "get_session_factory", lambda: lambda: nullcontext(db))
    with pytest.raises(RuntimeError, match="初期管理者の環境変数が未設定または不正です"), TestClient(app):
        pass
