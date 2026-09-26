"""
サービス層（業務処理）

【概要】
業務ルールの判断とトランザクション（commit）の管理を担当する。
本ファイルは、機能群ごとの実装に伴い関数が追加される。共通部分として、現在日時取得・今日取得・通知生成を持つ。
認証・ユーザー管理の機能群として、ログイン・パスワード変更・ユーザー管理・初期管理者作成の処理を持つ。
備品管理の機能群として、備品の検索・取得・登録・編集・分類一覧・予約状況・CSV一括登録の処理を持つ。

設計書：設計書/サーバー処理（main）/共通/、設計書/サーバー処理（main）/認証・ユーザー管理/、
設計書/サーバー処理（main）/備品管理/
"""

import csv
import io
import logging
import os
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app.auth import build_credentials_error, create_access_token, hash_password, verify_password
from app.crud import EquipmentCreateRow
from app.models import Equipment, LoanRequest, LoanStatus, NotificationType, Role, User
from app.schemas import (
    JST,
    AuthenticatedUser,
    CategoryListResponse,
    CsvImportResponse,
    CsvRowError,
    EquipmentCreateRequest,
    EquipmentListQuery,
    EquipmentResponse,
    EquipmentUpdateRequest,
    LoginRequest,
    MeResponse,
    Page,
    PasswordChangeRequest,
    PasswordResetRequest,
    ReservationListResponse,
    ReservationResponse,
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

_EQUIPMENT_NOT_FOUND_ERROR = "備品が見つかりません"
_ASSET_NUMBER_DUPLICATE_ERROR = "この資産番号は既に登録されています"
_EQUIPMENT_DEACTIVATE_ERROR = "申請中・承認済み・貸出中の申請がある備品は無効化できません"
_FORBIDDEN_ERROR = "この操作を行う権限がありません"

# 備品CSV一括登録の設定（列名・最小桁・最大桁。列の順序どおり）
_ASSET_NUMBER_PATTERN = r"[A-Za-z0-9-]+"
_CSV_COLUMNS = (
    ("資産番号", 1, 32),
    ("備品名", 1, 100),
    ("分類", 1, 50),
    ("説明", 0, 500),
    ("保管場所", 0, 100),
)
_CSV_MAX_ROWS = 1000
_CSV_MAX_ERRORS = 100
_LINE_BREAK_PATTERN = re.compile(r"\r\n|\r|\n")
_CSV_CONTENT_ERROR = "CSVの内容に誤りがあります"
_CSV_ENCODING_ERROR = "CSVの文字コードがUTF-8ではありません"
_CSV_FORMAT_ERROR = "CSVの形式が正しくありません"
_CSV_HEADER_ERROR = "ヘッダー行が正しくありません（資産番号,備品名,分類,説明,保管場所）"
_CSV_NO_DATA_ERROR = "登録するデータ行がありません"
_CSV_TOO_MANY_ROWS_ERROR = "データ行が上限（1,000行）を超えています"
_CSV_CONFLICT_ERROR = "資産番号が既に登録されています。再度お試しください"


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

    # 4. ユーザー作成・5. コミット（同時登録によるユーザーIDの重複はDBの一意制約で検出する）
    try:
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


# ---- 備品管理 ----


class CsvImportError(Exception):
    """
    CSV一括登録の内容エラー

    行別エラー（行番号・列名・エラー内容）を保持する。エンドポイント層の専用の例外ハンドラーが、
    ステータスコード400のCSVエラーレスポンス（CsvImportErrorResponse）へ変換する。
    """

    def __init__(self, errors: list[CsvRowError]) -> None:
        super().__init__(_CSV_CONTENT_ERROR)
        self.errors = errors


def _to_equipment_response(
    equipment: Equipment,
    lent_loan: LoanRequest | None,
    borrower_name: str | None,
    today: date,
    is_admin: bool,
) -> EquipmentResponse:
    """
    備品レスポンス変換（共通の内部処理）

    備品・貸出中の申請・借用者氏名から、備品レスポンスを作る。
    貸出状況は貸出中の申請の有無から算出し、借用者氏名は管理者にのみ設定する（一般ユーザーにはNULL）。
    期限超過は「貸出中かつ返却予定日が今日（JST）より前」の場合に真とする。
    """
    availability: Literal["available", "lent"] = "available"
    current_due_date = None
    is_overdue = False
    current_borrower_name = None
    if lent_loan is not None:
        availability = "lent"
        current_due_date = lent_loan.due_date
        is_overdue = lent_loan.due_date < today
        if is_admin:
            current_borrower_name = borrower_name
    equipment_response = EquipmentResponse(
        id=equipment.id,
        asset_number=equipment.asset_number,
        name=equipment.name,
        category=equipment.category,
        description=equipment.description,
        location=equipment.location,
        is_active=equipment.is_active,
        availability=availability,
        current_due_date=current_due_date,
        is_overdue=is_overdue,
        current_borrower_name=current_borrower_name,
        created_at=equipment.created_at,
        updated_at=equipment.updated_at,
    )
    return equipment_response


def _get_visible_equipment(db: Session, equipment_id: int, authenticated_user: AuthenticatedUser) -> Equipment:
    """
    閲覧可能な備品の取得（共通の内部処理）

    備品を内部IDで取得する。存在しない場合、および無効化済みで呼び出し元が管理者でない場合は、
    無効化済みの存在を一般ユーザーに知らせないため、いずれも404とする。
    """
    equipment = crud.get_equipment_by_id(db, equipment_id, False)
    if equipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_EQUIPMENT_NOT_FOUND_ERROR)
    is_admin = authenticated_user.role == Role.ADMIN.value
    if not equipment.is_active and not is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_EQUIPMENT_NOT_FOUND_ERROR)
    return equipment


def search_equipments(
    db: Session,
    authenticated_user: AuthenticatedUser,
    query: EquipmentListQuery,
) -> Page[EquipmentResponse]:
    """
    備品一覧検索処理

    設計書：設計書/サーバー処理（main）/備品管理/備品一覧検索

    【処理概要】
    - 備品を分類・キーワード・貸出状況で検索し、現在の貸出状況とともに一覧表示する。
    - 無効化済みの備品を含める指定は管理者のみ可能とする。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー
    - query (EquipmentListQuery) : 備品一覧クエリ

    【戻り値】
    - page_response (Page[EquipmentResponse]) : ページ形式の備品一覧

    【例外処理】
    - HTTPException(403) : 無効化済みを含める指定が、管理者以外から行われた場合

    【処理フロー】
    1. 権限の検証
    2. 備品一覧の取得（今日の取得・備品と現在の貸出状況の取得）
    3. 戻り値を設定
    """
    # 1. 権限の検証
    is_admin = authenticated_user.role == Role.ADMIN.value
    if query.include_inactive and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN_ERROR)

    # 2. 備品一覧の取得
    today = get_today()
    items, total = crud.get_equipments(
        db,
        query.keyword,
        query.category,
        query.availability,
        query.include_inactive,
        query.page,
        query.page_size,
    )

    # 3. 戻り値を設定
    responses = [
        _to_equipment_response(equipment, lent_loan, borrower_name, today, is_admin)
        for equipment, lent_loan, borrower_name in items
    ]
    page_response = Page[EquipmentResponse](
        items=responses,
        total=total,
        page=query.page,
        page_size=query.page_size,
    )
    return page_response


def get_equipment_detail(db: Session, authenticated_user: AuthenticatedUser, equipment_id: int) -> EquipmentResponse:
    """
    備品取得処理

    設計書：設計書/サーバー処理（main）/備品管理/備品取得

    【処理概要】
    - 備品1件の詳細と現在の貸出状況を取得する（備品詳細画面用）。
    - 無効化済みは、管理者以外には存在しないものとして扱う。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー
    - equipment_id (int) : 備品内部ID

    【戻り値】
    - equipment_response (EquipmentResponse) : 備品

    【例外処理】
    - HTTPException(404) : 備品が存在しない場合、または無効化済みで呼び出し元が管理者でない場合
      （"備品が見つかりません"）

    【処理フロー】
    1. 備品の取得
    2. 現在の貸出状況の取得（今日の取得・貸出中の申請の取得）
    3. 戻り値を設定
    """
    # 1. 備品の取得
    equipment = _get_visible_equipment(db, equipment_id, authenticated_user)

    # 2. 現在の貸出状況の取得
    today = get_today()
    lent_result = crud.get_lent_loan_by_equipment(db, equipment_id)
    lent_loan = None
    borrower_name = None
    if lent_result is not None:
        lent_loan, borrower_name = lent_result

    # 3. 戻り値を設定
    is_admin = authenticated_user.role == Role.ADMIN.value
    equipment_response = _to_equipment_response(equipment, lent_loan, borrower_name, today, is_admin)
    return equipment_response


def list_equipment_categories(db: Session) -> CategoryListResponse:
    """
    分類一覧取得処理

    設計書：設計書/サーバー処理（main）/備品管理/分類一覧取得

    【処理概要】
    - 備品検索の絞り込み選択肢として、有効な備品の分類を返す。

    【パラメータ】
    - db (Session) : DBセッション

    【戻り値】
    - category_response (CategoryListResponse) : 分類一覧（0件は空配列）

    【例外処理】
    - なし

    【処理フロー】
    1. 分類の取得
    2. 戻り値を設定
    """
    # 1. 分類の取得
    categories = crud.get_categories(db)

    # 2. 戻り値を設定
    category_response = CategoryListResponse(items=categories)
    return category_response


def get_equipment_reservations(
    db: Session,
    authenticated_user: AuthenticatedUser,
    equipment_id: int,
) -> ReservationListResponse:
    """
    予約状況取得処理

    設計書：設計書/サーバー処理（main）/備品管理/予約状況取得

    【処理概要】
    - 備品の承認済み・貸出中の期間を返し、利用者が空き期間を把握できるようにする。
    - 借用者氏名は管理者にのみ返す。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー
    - equipment_id (int) : 備品内部ID

    【戻り値】
    - reservation_response (ReservationListResponse) : 予約状況（開始日の昇順。0件は空配列）

    【例外処理】
    - HTTPException(404) : 備品が存在しない場合、または無効化済みで呼び出し元が管理者でない場合
      （"備品が見つかりません"）

    【処理フロー】
    1. 備品の確認
    2. 予約期間の取得（今日の取得・予約期間一覧の取得）
    3. 戻り値を設定（貸出中の占有終了日は、返却予定日と今日のうち遅い方）
    """
    # 1. 備品の確認
    _get_visible_equipment(db, equipment_id, authenticated_user)

    # 2. 予約期間の取得
    today = get_today()
    reservations = crud.get_reservations(db, equipment_id, today)

    # 3. 戻り値を設定
    is_admin = authenticated_user.role == Role.ADMIN.value
    items = []
    for loan, borrower_name in reservations:
        occupied_until = loan.due_date
        if loan.status == LoanStatus.LENT.value and today > loan.due_date:
            occupied_until = today
        reservation = ReservationResponse(
            start_date=loan.start_date,
            due_date=loan.due_date,
            occupied_until=occupied_until,
            status="lent" if loan.status == LoanStatus.LENT.value else "approved",
            borrower_name=borrower_name if is_admin else None,
        )
        items.append(reservation)
    reservation_response = ReservationListResponse(items=items)
    return reservation_response


def register_equipment(db: Session, request: EquipmentCreateRequest) -> EquipmentResponse:
    """
    備品登録処理

    設計書：設計書/サーバー処理（main）/備品管理/備品登録

    【処理概要】
    - 管理者が備品を1点登録する。資産番号の重複を検証し、有効な備品として登録する。

    【パラメータ】
    - db (Session) : DBセッション
    - request (EquipmentCreateRequest) : 備品登録リクエスト

    【戻り値】
    - equipment_response (EquipmentResponse) : 登録した備品
      （貸出状況available・現在の返却予定日NULL・期限超過False・借用者氏名NULL）

    【例外処理】
    - HTTPException(409) : 資産番号が既に登録されている場合（無効化済みを含む。"この資産番号は既に登録されています"）

    【処理フロー】
    1. 現在日時の取得
    2. 資産番号の重複確認
    3. 備品の登録
    4. コミット（同時登録による一意制約違反は409）
    5. 戻り値を設定
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. 資産番号の重複確認
    existing_equipment = crud.get_equipment_by_asset_number(db, request.asset_number)
    if existing_equipment is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ASSET_NUMBER_DUPLICATE_ERROR)

    # 3. 備品の登録・4. コミット（同時登録による資産番号の重複はDBの一意制約で検出する）
    try:
        equipment = crud.create_equipment(
            db,
            request.asset_number,
            request.name,
            request.category,
            request.description,
            request.location,
            now,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ASSET_NUMBER_DUPLICATE_ERROR) from None

    # 5. 戻り値を設定（登録直後は貸出中の申請がない）
    today = get_today()
    equipment_response = _to_equipment_response(equipment, None, None, today, True)
    return equipment_response


def update_equipment_admin(db: Session, equipment_id: int, request: EquipmentUpdateRequest) -> EquipmentResponse:
    """
    備品編集処理

    設計書：設計書/サーバー処理（main）/備品管理/備品編集

    【処理概要】
    - 管理者が備品の内容を更新する。有効フラグによる無効化・再有効化を含む。資産番号は変更しない。
    - 備品を行ロックして取得し、無効化時は未完了の申請がないことを検証したうえで更新する。

    【パラメータ】
    - db (Session) : DBセッション
    - equipment_id (int) : 備品内部ID
    - request (EquipmentUpdateRequest) : 備品編集リクエスト

    【戻り値】
    - equipment_response (EquipmentResponse) : 更新後の備品（管理者のため借用者氏名を設定）

    【例外処理】
    - HTTPException(404) : 備品が存在しない場合（"備品が見つかりません"）
    - HTTPException(400) : 申請中・承認済み・貸出中の申請がある備品を無効化する場合
      （"申請中・承認済み・貸出中の申請がある備品は無効化できません"）

    【処理フロー】
    1. 対象備品の取得とロック（現在日時の取得・備品の行ロック取得）
    2. 無効化に該当する場合、未完了の申請がないことを検証
    3. 備品の更新
    4. 貸出中の申請の取得・今日の取得
    5. コミット
    6. 戻り値を設定
    """
    # 1. 対象備品の取得とロック（貸出申請・承認・貸出の各処理も同じ行ロックを取るため、競合しても整合が保たれる）
    now = get_now()
    equipment = crud.get_equipment_by_id(db, equipment_id, True)
    if equipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_EQUIPMENT_NOT_FOUND_ERROR)

    # 2. 無効化に該当する場合、未完了の申請がないことを検証（再有効化・内容のみの更新は何もしない）
    is_deactivation = equipment.is_active and not request.is_active
    if is_deactivation:
        open_count = crud.count_open_loans_by_equipment(db, equipment_id)
        if open_count >= 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_EQUIPMENT_DEACTIVATE_ERROR)

    # 3. 備品の更新
    crud.update_equipment(
        db,
        equipment,
        request.name,
        request.category,
        request.description,
        request.location,
        request.is_active,
        now,
    )

    # 4. 貸出中の申請の取得（無効化されていない備品は貸出中の申請を持ちうるため、常に取得して返却に反映する）
    lent_result = crud.get_lent_loan_by_equipment(db, equipment_id)
    lent_loan = None
    borrower_name = None
    if lent_result is not None:
        lent_loan, borrower_name = lent_result
    today = get_today()

    # 5. コミット
    db.commit()

    # 6. 戻り値を設定
    equipment_response = _to_equipment_response(equipment, lent_loan, borrower_name, today, True)
    return equipment_response


def _decode_csv_bytes(content: bytes) -> str:
    """CSVのバイト列をUTF-8（BOMの有無は問わない）でデコードする。デコードできない場合は400"""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_CSV_ENCODING_ERROR) from None
    return text


def _parse_csv_rows(text: str) -> list[tuple[int, list[str]]]:
    """
    CSVを解析し、（ファイル上の行番号, セル一覧）の一覧を返す（標準のCSVパーサーを使用）

    引用符・区切り文字・セル内改行に対応する。全セルが空の行（空行）は読み飛ばす。
    行番号はヘッダー行を1行目とする、人が確認できるファイル上の行番号とする。
    解析できない場合は400。
    """
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    rows: list[tuple[int, list[str]]] = []
    try:
        for cells in reader:
            # reader.line_numは、セル内改行を含む場合その行の最後の物理行を指すため、開始行を求める
            start_line = reader.line_num
            for cell in cells:
                line_breaks = _LINE_BREAK_PATTERN.findall(cell)
                start_line -= len(line_breaks)
            if any(cell.strip() for cell in cells):
                rows.append((start_line, cells))
    except csv.Error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_CSV_FORMAT_ERROR) from None
    return rows


def _has_control_char(value: str, allow_line_break: bool) -> bool:
    """制御文字（改行・タブを含む）が含まれるかを判定する。allow_line_breakが真の場合、改行（CR・LF）は許容する"""
    for char in value:
        if allow_line_break and char in "\r\n":
            continue
        if unicodedata.category(char) == "Cc":
            return True
    return False


def _validate_csv_row(row_number: int, cells: list[str]) -> tuple[EquipmentCreateRow | None, list[CsvRowError]]:
    """
    CSVのデータ1行を検証する（共通の内部処理）

    列数・各項目の桁数（前後の空白を除去して判定）・資産番号の形式・制御文字を検証する。
    誤りがなければ（前後の空白を除去した登録内容, 空リスト）、誤りがあれば（None, 行別エラー一覧）を返す。
    """
    if len(cells) != len(_CSV_COLUMNS):
        return None, [CsvRowError(row_number=row_number, column=None, message="列数が正しくありません")]

    errors: list[CsvRowError] = []
    values: list[str] = []
    for index, (column_name, min_length, max_length) in enumerate(_CSV_COLUMNS):
        value = cells[index].strip()
        values.append(value)
        if not (min_length <= len(value) <= max_length):
            message = f"{column_name}は{min_length}〜{max_length}文字で入力してください"
            errors.append(CsvRowError(row_number=row_number, column=column_name, message=message))
            continue
        # 説明のみ改行を許容する
        allow_line_break = column_name == "説明"
        if _has_control_char(value, allow_line_break):
            message = f"{column_name}に使用できない文字が含まれています"
            errors.append(CsvRowError(row_number=row_number, column=column_name, message=message))
            continue
        if column_name == "資産番号" and not re.fullmatch(_ASSET_NUMBER_PATTERN, value):
            message = "資産番号は半角英数字と-のみ使用できます"
            errors.append(CsvRowError(row_number=row_number, column=column_name, message=message))
    if errors:
        return None, errors
    row = EquipmentCreateRow(
        asset_number=values[0],
        name=values[1],
        category=values[2],
        description=values[3],
        location=values[4],
    )
    return row, []


def _get_error_row_number(error: CsvRowError) -> int:
    """行別エラーの行番号を返す（行番号の昇順に並べるための取得関数。同じ行内では発生順を保つ）"""
    return error.row_number


def import_equipments_csv(db: Session, content: bytes) -> CsvImportResponse:
    """
    備品CSV一括登録処理

    設計書：設計書/サーバー処理（main）/備品管理/備品CSV一括登録

    【処理概要】
    - 管理者が、CSVファイルから備品を一括登録する。
    - ファイルの文字コード・ヘッダー・行数を検証し、全行を検証したうえで、誤りが1行もない場合のみ全件を登録する
      （全件成功または全件失敗）。ファイルサイズの検証はエンドポイント層で行う。

    【パラメータ】
    - db (Session) : DBセッション
    - content (bytes) : CSVファイルの内容（サイズ検証済み）

    【戻り値】
    - import_response (CsvImportResponse) : 登録件数

    【例外処理】
    - HTTPException(400) : 文字コードがUTF-8でない・CSVの形式が正しくない・ヘッダー行が正しくない・データ行が0件・
      データ行が上限（1,000行）を超える場合
    - CsvImportError : 行ごとの検証エラー（最大100件）が1件以上ある場合（400。1件も登録しない）
    - HTTPException(409) : 検証後の同時登録により資産番号が重複した場合（1件も登録されない）

    【処理フロー】
    1. 現在日時の取得
    2. UTF-8（BOM許容）でデコード
    3. CSVとして解析
    4. ヘッダー行と行数の検証
    5. 各データ行の検証（全行分のエラーを収集。最大100件で打ち切る）
    6. 既存の資産番号との重複検証
    7. エラー判定
    8. 備品の一括登録
    9. コミット（同時登録による一意制約違反は409）
    10. 登録件数のログ記録・戻り値の設定
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. UTF-8（BOM許容）でデコード
    text = _decode_csv_bytes(content)

    # 3. CSVとして解析
    parsed_rows = _parse_csv_rows(text)

    # 4. ヘッダー行と行数の検証
    expected_header = [column[0] for column in _CSV_COLUMNS]
    header_cells = None
    if parsed_rows:
        header_cells = parsed_rows[0][1]
    if header_cells != expected_header:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_CSV_HEADER_ERROR)
    data_rows = parsed_rows[1:]
    if len(data_rows) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_CSV_NO_DATA_ERROR)
    if len(data_rows) > _CSV_MAX_ROWS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_CSV_TOO_MANY_ROWS_ERROR)

    # 5. 各データ行の検証
    errors: list[CsvRowError] = []
    valid_rows: list[tuple[int, EquipmentCreateRow]] = []
    first_row_by_asset_number: dict[str, int] = {}
    for row_number, cells in data_rows:
        row, row_errors = _validate_csv_row(row_number, cells)
        errors.extend(row_errors)
        if row is None:
            continue
        first_row_number = first_row_by_asset_number.get(row.asset_number)
        if first_row_number is not None:
            message = f"資産番号がファイル内で重複しています（{first_row_number}行目）"
            errors.append(CsvRowError(row_number=row_number, column="資産番号", message=message))
            continue
        first_row_by_asset_number[row.asset_number] = row_number
        valid_rows.append((row_number, row))

    # 6. 既存の資産番号との重複検証（5.でエラーがなかった資産番号のみ対象）
    asset_numbers = [row.asset_number for _, row in valid_rows]
    existing_asset_numbers = crud.get_existing_asset_numbers(db, asset_numbers)
    for row_number, row in valid_rows:
        if row.asset_number in existing_asset_numbers:
            message = "資産番号が既に登録されています"
            errors.append(CsvRowError(row_number=row_number, column="資産番号", message=message))

    # 7. エラー判定（行番号の昇順・最大100件）
    if errors:
        errors.sort(key=_get_error_row_number)
        raise CsvImportError(errors[:_CSV_MAX_ERRORS])

    # 8. 備品の一括登録・9. コミット（同時登録による資産番号の重複はDBの一意制約で検出する）
    rows = [row for _, row in valid_rows]
    try:
        imported_count = crud.create_equipments(db, rows, now)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_CSV_CONFLICT_ERROR) from None

    # 10. 登録件数のログ記録（件数のみ。CSVの内容は記録しない）・戻り値の設定
    logger.info("備品CSV一括登録: 登録件数=%d", imported_count)
    import_response = CsvImportResponse(imported_count=imported_count)
    return import_response
