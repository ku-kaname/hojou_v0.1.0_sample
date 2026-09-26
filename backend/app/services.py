"""
サービス層（業務処理）

【概要】
業務ルールの判断とトランザクション（commit）の管理を担当する。
本ファイルは、機能群ごとの実装に伴い関数が追加される。共通部分として、現在日時取得・今日取得・通知生成を持つ。
認証・ユーザー管理の機能群として、ログイン・パスワード変更・ユーザー管理・初期管理者作成の処理を持つ。

設計書：設計書/サーバー処理（main）/共通/、設計書/サーバー処理（main）/認証・ユーザー管理/
"""

import logging
import os
import re
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app.auth import build_credentials_error, create_access_token, hash_password, verify_password
from app.models import NotificationType, Role, User
from app.schemas import (
    JST,
    AuthenticatedUser,
    LoginRequest,
    MeResponse,
    Page,
    PasswordChangeRequest,
    PasswordResetRequest,
    TokenResponse,
    UserCreateRequest,
    UserListQuery,
    UserResponse,
    UserUpdateRequest,
)

logger = logging.getLogger("app")

# 連続認証失敗でロックする回数と、ロックする時間（分）
MAX_FAILED_LOGIN_COUNT = 5
LOCK_MINUTES = 15

_CURRENT_PASSWORD_ERROR = "現在のパスワードが正しくありません"
_LOGIN_ID_DUPLICATE_ERROR = "このユーザーIDは既に登録されています"
_USER_NOT_FOUND_ERROR = "ユーザーが見つかりません"


def get_now() -> datetime:
    """
    現在日時取得

    設計書：設計書/サーバー処理（main）/共通/日時取得

    【処理概要】
    - 業務処理が現在日時を直接取得せず、本関数経由で取得する（テスト時に差し替えられるようにする）。
    - UTCのタイムゾーン付き現在日時を返却する（DB保存用）。

    【パラメータ】
    - なし

    【戻り値】
    - now (datetime) : 現在日時（UTC・タイムゾーン付き）

    【例外処理】
    - なし

    【処理フロー】
    1. サーバー時刻をUTCのタイムゾーン付きで取得
    2. 戻り値を設定
    """
    now = datetime.now(UTC)
    return now


def get_today() -> date:
    """
    今日取得

    設計書：設計書/サーバー処理（main）/共通/日時取得

    【処理概要】
    - 現在日時をJST（Asia/Tokyo）へ変換した暦日を返却する（期間・期限の判定用）。

    【パラメータ】
    - なし

    【戻り値】
    - today (date) : 今日（JSTの暦日）

    【例外処理】
    - なし

    【処理フロー】
    1. 現在日時取得の結果をJSTへ変換し、日付部分を取得
    2. 戻り値を設定
    """
    now = get_now()
    jst_now = now.astimezone(JST)
    return jst_now.date()


def create_notifications(
    db: Session,
    recipient_ids: list[int],
    notification_type: str,
    loan_request_id: int,
    notified_date: date,
) -> None:
    """
    通知生成

    設計書：設計書/サーバー処理（main）/共通/通知生成

    【処理概要】
    - 宛先・種別・関連申請から通知レコードを生成する。将来メール等の外部通知を追加する際の差し替え点とする。
    - 呼び出し元のトランザクションに含める（本関数自体はcommitしない）。

    【パラメータ】
    - db (Session) : DBセッション
    - recipient_ids (list[int]) : 宛先（ユーザー内部ID）一覧。重複は除外して処理する
    - notification_type (str) : 種別（通知種別の列挙値）
    - loan_request_id (int) : 関連申請（内部ID）
    - notified_date (date) : 通知日（呼び出し元が今日取得で得たJSTの暦日）

    【戻り値】
    - なし

    【例外処理】
    - なし（呼び出し元の内部処理からのみ呼ばれるため入力検証は行わない）

    【処理フロー】
    1. 宛先の重複を除き、宛先ごとに通知登録を呼び出す
       - 日次通知（返却期限）は、同日に生成済みの宛先を読み飛ばす（通知登録側で判定）
    2. 戻り値を設定（なし）
    """
    # 1. 通知の生成（重複除外は、先に現れた宛先の順序を保つ）
    ordered_recipients = dict.fromkeys(recipient_ids)
    unique_recipient_ids = list(ordered_recipients)
    for recipient_id in unique_recipient_ids:
        crud.create_notification(db, recipient_id, notification_type, loan_request_id, notified_date)


# ---- 認証・ユーザー管理 ----


def _release_expired_lock(db: Session, user: User, now: datetime) -> None:
    """ロック期間が経過している場合に、連続認証失敗回数とロックを初期化する（ログイン・パスワード変更で共通）"""
    if user.locked_until is not None and user.locked_until <= now:
        crud.update_login_state(db, user, 0, None, now)


def _is_locked(user: User, now: datetime) -> bool:
    """ユーザーがロック中（ロック解除日時が現在日時より後）かどうかを返す"""
    is_locked = user.locked_until is not None and user.locked_until > now
    return is_locked


def _record_password_failure(db: Session, user: User, now: datetime) -> None:
    """パスワード不一致を記録する。失敗回数を+1し、上限に達したらロック解除日時を設定して保存（flush）する"""
    new_failed_count = user.failed_login_count + 1
    new_locked_until = user.locked_until
    if new_failed_count >= MAX_FAILED_LOGIN_COUNT:
        new_locked_until = now + timedelta(minutes=LOCK_MINUTES)
    crud.update_login_state(db, user, new_failed_count, new_locked_until, now)


def login_user(db: Session, request: LoginRequest) -> TokenResponse | None:
    """
    ログイン認証

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ログイン

    【処理概要】
    - ユーザーID・パスワードで認証し、アクセストークンを発行する。
    - 連続認証失敗（5回）で15分間ロックする。
    - ユーザー列挙・タイミング差による推測を防ぐため、失敗の原因によらず同一の結果（None）・同等の処理時間とする。

    【パラメータ】
    - db (Session) : DBセッション
    - request (LoginRequest) : ログインリクエスト（ユーザーID・パスワード）

    【戻り値】
    - token_response (TokenResponse | None) : トークン発行レスポンス（認証失敗はNone）

    【例外処理】
    - なし（認証失敗はNoneで返し、エンドポイント層が401にする）

    【処理フロー】
    1. 現在日時の取得
    2. ユーザーをユーザーIDで行ロックして取得
    3. ロック期間が経過している場合は失敗状態を初期化
    4. パスワード検証（ユーザーの有無によらず必ず1回実行）
    5. 認証結果の判定（不在・無効・ロック中は失敗、不一致は失敗回数加算、一致はトークン発行）
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. ユーザー取得（行ロック）
    user = crud.get_user_by_login_id(db, request.login_id, True)

    # 3. ロック期間経過時の初期化
    if user is not None:
        _release_expired_lock(db, user, now)

    # 4. パスワード検証（処理時間の差でユーザーの存在を推測されないよう、常に1回実行する）
    stored_hash = user.password_hash if user is not None else None
    is_password_valid = verify_password(request.password, stored_hash)

    # 5. 認証結果の判定
    if user is None:
        db.rollback()
        return None
    if not user.is_active:
        db.commit()
        logger.warning("ログイン失敗: ユーザー内部ID=%s 結果=無効ユーザー", user.id)
        return None
    if _is_locked(user, now):
        db.commit()
        logger.warning("ログイン失敗: ユーザー内部ID=%s 結果=ロック中", user.id)
        return None
    if not is_password_valid:
        _record_password_failure(db, user, now)
        db.commit()
        logger.warning("ログイン失敗: ユーザー内部ID=%s 結果=パスワード不一致", user.id)
        return None

    crud.update_login_state(db, user, 0, None, now)
    access_token = create_access_token(user.id, user.token_generation, now)
    db.commit()
    token_response = TokenResponse(
        access_token=access_token,
        token_type="bearer",
        must_change_password=user.must_change_password,
    )
    return token_response


def logout_user(db: Session, authenticated_user: AuthenticatedUser) -> None:
    """
    ログアウト処理

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ログアウト

    【処理概要】
    - ユーザーのトークン世代を+1し、発行済みトークンをすべて失効させる。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー

    【戻り値】
    - なし

    【例外処理】
    - なし（認証エラーは認証・認可層が401を返す）

    【処理フロー】
    1. 現在日時の取得
    2. ユーザーを内部IDで行ロックして取得
    3. トークン世代の更新
    4. コミット
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. ユーザー取得（行ロック）
    user = crud.get_user_by_id(db, authenticated_user.id, True)
    if user is None:
        raise build_credentials_error()

    # 3. トークン世代の更新
    crud.increment_token_generation(db, user, now)

    # 4. コミット
    db.commit()


def get_my_profile(db: Session, authenticated_user: AuthenticatedUser) -> MeResponse:
    """
    自分の情報取得

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/自分の情報取得

    【処理概要】
    - ログイン中のユーザー自身の公開項目を返却する（画面の表示制御用）。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー

    【戻り値】
    - me_response (MeResponse) : 自分のユーザー情報

    【例外処理】
    - HTTPException(401) : ユーザーが取得できない場合（認証直後の削除等。"資格情報を検証できませんでした"）

    【処理フロー】
    1. ユーザーを内部IDで取得
    2. 例外処理・戻り値を設定
    """
    # 1. ユーザー取得
    user = crud.get_user_by_id(db, authenticated_user.id, False)

    # 2. 例外処理・戻り値の設定
    if user is None:
        raise build_credentials_error()
    me_response = MeResponse.model_validate(user)
    return me_response


def change_own_password(
    db: Session,
    authenticated_user: AuthenticatedUser,
    request: PasswordChangeRequest,
) -> TokenResponse:
    """
    パスワード変更処理

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/パスワード変更

    【処理概要】
    - ログイン中のユーザーが自分のパスワードを変更する（初回パスワード変更を含む）。
    - 現在のパスワードを検証し、新しいパスワードのハッシュを保存して発行済みトークンを失効させ、新しいトークンを返す。
    - 現在のパスワードの誤りはログインと同じ失敗回数・ロックを共用する（盗用トークンによる総当たり対策）。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー
    - request (PasswordChangeRequest) : パスワード変更リクエスト

    【戻り値】
    - token_response (TokenResponse) : 新しいトークン（初回パスワード変更要否は偽）

    【例外処理】
    - HTTPException(400) : 現在のパスワードが正しくない・ロック中（"現在のパスワードが正しくありません"）
    - HTTPException(400) : 新しいパスワードが現在のパスワードと同一
      （"新しいパスワードは現在のパスワードと異なる必要があります"）
    - HTTPException(401) : ユーザーが取得できない場合（"資格情報を検証できませんでした"）

    【処理フロー】
    1. 現在日時の取得
    2. ユーザーを内部IDで行ロックして取得
    3. ロック期間が経過している場合は失敗状態を初期化
    4. 現在のパスワードの検証
    5. 検証結果の判定
    6. 新しいパスワードのハッシュ化
    7. パスワード更新
    8. 新しいトークンの発行
    9. コミット
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. ユーザー取得（行ロック）
    user = crud.get_user_by_id(db, authenticated_user.id, True)
    if user is None:
        raise build_credentials_error()

    # 3. ロック期間経過時の初期化
    _release_expired_lock(db, user, now)

    # 4. 現在のパスワードの検証
    is_password_valid = verify_password(request.current_password, user.password_hash)

    # 5. 検証結果の判定
    if _is_locked(user, now):
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_CURRENT_PASSWORD_ERROR)
    if not is_password_valid:
        # 例外送出前に失敗回数・ロックを保存する
        _record_password_failure(db, user, now)
        db.commit()
        logger.warning("パスワード変更失敗: ユーザー内部ID=%s 結果=現在のパスワード不一致", user.id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_CURRENT_PASSWORD_ERROR)
    if request.new_password == request.current_password:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新しいパスワードは現在のパスワードと異なる必要があります",
        )

    # 6. 新しいパスワードのハッシュ化
    new_password_hash = hash_password(request.new_password)

    # 7. パスワード更新（トークン世代の+1を含む）
    crud.update_password(db, user, new_password_hash, False, now)

    # 8. 新しいトークンの発行
    access_token = create_access_token(user.id, user.token_generation, now)

    # 9. コミット
    db.commit()
    token_response = TokenResponse(access_token=access_token, token_type="bearer", must_change_password=False)
    return token_response


def register_user(db: Session, request: UserCreateRequest) -> UserResponse:
    """
    ユーザー登録処理

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ユーザー登録

    【処理概要】
    - 管理者が新しいユーザーを登録する。初期パスワードは管理者が設定し、初回ログイン時に変更を強制する。

    【パラメータ】
    - db (Session) : DBセッション
    - request (UserCreateRequest) : ユーザー登録リクエスト

    【戻り値】
    - user_response (UserResponse) : 登録したユーザー

    【例外処理】
    - HTTPException(409) : ユーザーIDが既に登録されている場合
      （無効化済みを含む。"このユーザーIDは既に登録されています"）

    【処理フロー】
    1. 現在日時の取得
    2. ユーザーIDの重複確認
    3. 初期パスワードのハッシュ化
    4. ユーザー作成
    5. コミット（同時登録による一意制約違反は409）
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. ユーザーIDの重複確認
    existing_user = crud.get_user_by_login_id(db, request.login_id, False)
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_LOGIN_ID_DUPLICATE_ERROR)

    # 3. 初期パスワードのハッシュ化
    password_hash = hash_password(request.initial_password)

    # 4. ユーザー作成
    user = crud.create_user(
        db,
        request.login_id,
        request.name,
        request.department,
        password_hash,
        request.role,
        True,
        now,
    )

    # 5. コミット
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_LOGIN_ID_DUPLICATE_ERROR) from None
    user_response = UserResponse.model_validate(user)
    return user_response


def list_users_admin(db: Session, query: UserListQuery) -> Page[UserResponse]:
    """
    ユーザー一覧取得処理

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ユーザー一覧取得

    【処理概要】
    - 管理者が、無効化済みを含むユーザーを絞り込み・ページングで取得する。

    【パラメータ】
    - db (Session) : DBセッション
    - query (UserListQuery) : ユーザー一覧クエリ

    【戻り値】
    - page_response (Page[UserResponse]) : ページ形式のユーザー一覧

    【例外処理】
    - なし

    【処理フロー】
    1. 絞り込み条件・ページングでユーザーを取得
    2. 戻り値を設定
    """
    # 1. ユーザー取得
    users, total = crud.get_users(db, query.keyword, query.role, query.is_active, query.page, query.page_size)

    # 2. 戻り値を設定
    items = [UserResponse.model_validate(user) for user in users]
    page_response = Page[UserResponse](items=items, total=total, page=query.page, page_size=query.page_size)
    return page_response


def get_user_admin(db: Session, user_id: int) -> UserResponse:
    """
    ユーザー取得処理

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ユーザー取得

    【処理概要】
    - 管理者が、ユーザー1件の詳細を取得する（編集画面の初期表示用）。

    【パラメータ】
    - db (Session) : DBセッション
    - user_id (int) : ユーザー内部ID

    【戻り値】
    - user_response (UserResponse) : ユーザー

    【例外処理】
    - HTTPException(404) : ユーザーが存在しない場合（"ユーザーが見つかりません"）

    【処理フロー】
    1. ユーザーを内部IDで取得
    2. 例外処理・戻り値を設定
    """
    # 1. ユーザー取得
    user = crud.get_user_by_id(db, user_id, False)

    # 2. 例外処理・戻り値の設定
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND_ERROR)
    user_response = UserResponse.model_validate(user)
    return user_response


def update_user_admin(db: Session, user_id: int, request: UserUpdateRequest) -> UserResponse:
    """
    ユーザー編集処理

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/ユーザー編集

    【処理概要】
    - 管理者がユーザーの氏名・所属・ロール・有効フラグ（無効化・再有効化）を更新する。
    - 有効な管理者が0人にならないことを保証する。
    - 無効化時は、未貸出の申請を自動取消して通知し、貸出中の申請がある場合は無効化させない。
    - 無効化・ロール変更時は発行済みトークンを失効させる。

    【パラメータ】
    - db (Session) : DBセッション
    - user_id (int) : ユーザー内部ID
    - request (UserUpdateRequest) : ユーザー編集リクエスト

    【戻り値】
    - user_response (UserResponse) : 更新後のユーザー

    【例外処理】
    - HTTPException(404) : ユーザーが存在しない場合（"ユーザーが見つかりません"）
    - HTTPException(400) : 最後の有効な管理者の無効化・ロール変更（"最後の有効な管理者は無効化・ロール変更できません"）
    - HTTPException(400) : 貸出中の申請があるユーザーの無効化（"貸出中の申請があるユーザーは無効化できません"）

    【処理フロー】
    1. 現在日時の取得
    2. 有効な管理者を内部ID昇順で行ロック（ロック順は「管理者全員→対象ユーザー」で統一）
    3. 対象ユーザーを内部IDで行ロックして取得
    4. 変更内容の判定（無効化・再有効化・ロール変更・管理者喪失）
    5. 業務ルールの検証
    6. ユーザー情報の更新
    7. 再有効化・無効化・ロール変更に応じた追加処理
    8. コミット
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. 有効な管理者の行ロック
    active_admin_ids = crud.lock_active_admin_ids(db)

    # 3. 対象ユーザーの取得（行ロック）
    user = crud.get_user_by_id(db, user_id, True)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND_ERROR)

    # 4. 変更内容の判定
    is_deactivation = user.is_active and not request.is_active
    is_reactivation = not user.is_active and request.is_active
    is_role_change = user.role != request.role
    is_admin_demotion = is_role_change and request.role != Role.ADMIN.value
    is_admin_loss = user.id in active_admin_ids and (is_deactivation or is_admin_demotion)

    # 5. 業務ルールの検証
    if is_admin_loss and active_admin_ids == [user.id]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="最後の有効な管理者は無効化・ロール変更できません",
        )
    if is_deactivation:
        lent_count = crud.count_lent_by_requester(db, user.id)
        if lent_count >= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="貸出中の申請があるユーザーは無効化できません",
            )

    # 6. ユーザー情報の更新
    crud.update_user_profile(db, user, request.name, request.department, request.role, request.is_active, now)

    # 7. 追加処理
    if is_reactivation:
        crud.update_login_state(db, user, 0, None, now)
    if is_deactivation:
        canceled_loan_requests = crud.cancel_pending_by_requester(db, user.id, "ユーザー無効化に伴う自動取消", now)
        today = get_today()
        for canceled_loan_request in canceled_loan_requests:
            create_notifications(
                db,
                [user.id],
                NotificationType.CANCELED.value,
                canceled_loan_request.id,
                today,
            )
    if is_role_change or is_deactivation:
        # 発行済みトークンを失効させる
        crud.increment_token_generation(db, user, now)

    # 8. コミット
    db.commit()
    user_response = UserResponse.model_validate(user)
    return user_response


def reset_password_admin(db: Session, user_id: int, request: PasswordResetRequest) -> None:
    """
    パスワード初期化処理

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/パスワード初期化

    【処理概要】
    - 管理者が、パスワードを忘れたユーザーのパスワードを初期パスワードに再設定する。
    - 初回パスワード変更を要求し、発行済みトークンの失効とロック解除を行う。

    【パラメータ】
    - db (Session) : DBセッション
    - user_id (int) : ユーザー内部ID
    - request (PasswordResetRequest) : パスワード初期化リクエスト

    【戻り値】
    - なし

    【例外処理】
    - HTTPException(404) : ユーザーが存在しない場合（"ユーザーが見つかりません"）

    【処理フロー】
    1. 現在日時の取得
    2. 対象ユーザーを内部IDで行ロックして取得
    3. 新しいパスワードのハッシュ化
    4. パスワード更新（トークン世代+1・失敗回数0・ロック解除を含む）
    5. コミット
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. 対象ユーザーの取得（行ロック）
    user = crud.get_user_by_id(db, user_id, True)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND_ERROR)

    # 3. 新しいパスワードのハッシュ化
    password_hash = hash_password(request.new_password)

    # 4. パスワード更新
    crud.update_password(db, user, password_hash, True, now)

    # 5. コミット
    db.commit()


def ensure_initial_admin(db: Session) -> None:
    """
    初期管理者作成処理

    設計書：設計書/サーバー処理（main）/認証・ユーザー管理/初期管理者作成

    【処理概要】
    - システム導入直後に管理者が1人もいない状態を解消するため、起動時に初期管理者を1人作成する。
    - 有効な管理者が既に存在する場合は何もしない（再起動しても重複作成しない）。
    - 環境変数INITIAL_ADMIN_LOGIN_ID・INITIAL_ADMIN_PASSWORDの値で作成する（パスワードはログに記録しない）。

    【パラメータ】
    - db (Session) : DBセッション（起動処理が生成したもの）

    【戻り値】
    - なし

    【例外処理】
    - RuntimeError : 管理者がいない状態で環境変数が未設定・不正な場合（"初期管理者の環境変数が未設定または不正です"）
    - RuntimeError : 初期管理者のユーザーIDが既存ユーザーと重複している場合

    【処理フロー】
    1. 現在日時の取得
    2. 有効な管理者IDの取得（1件以上あれば終了）
    3. 環境変数の検証
    4. ユーザーIDの重複確認
    5. パスワードのハッシュ化
    6. 管理者の作成
    7. コミット（複数プロセスの同時起動による一意制約違反は、ロールバックして管理者の存在を再確認）
    8. 作成した旨をログに記録
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. 有効な管理者IDの取得
    active_admin_ids = crud.get_active_admin_ids(db)
    if active_admin_ids:
        return

    # 3. 環境変数の検証
    login_id = os.environ.get("INITIAL_ADMIN_LOGIN_ID", "")
    password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
    is_login_id_valid = 3 <= len(login_id) <= 32 and re.fullmatch(r"[A-Za-z0-9_-]+", login_id) is not None
    is_password_valid = 8 <= len(password) <= 128
    if not (is_login_id_valid and is_password_valid):
        raise RuntimeError("初期管理者の環境変数が未設定または不正です")

    # 4. ユーザーIDの重複確認
    existing_user = crud.get_user_by_login_id(db, login_id, False)
    if existing_user is not None:
        raise RuntimeError("初期管理者のユーザーIDが既存ユーザーと重複しています")

    # 5. パスワードのハッシュ化
    password_hash = hash_password(password)

    # 6. 管理者の作成
    crud.create_user(db, login_id, "初期管理者", "", password_hash, Role.ADMIN.value, True, now)

    # 7. コミット
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # 他のプロセスが先に作成した場合は正常終了する
        active_admin_ids = crud.get_active_admin_ids(db)
        if active_admin_ids:
            return
        raise

    # 8. ログ記録（パスワードは記録しない）
    logger.info("初期管理者を作成しました: ユーザーID=%s", login_id)
