"""
サービス層（業務処理）

【概要】
業務ルールの判断とトランザクション（commit）の管理を担当する。
本ファイルは、機能群ごとの実装に伴い関数が追加される。共通部分として、現在日時取得・今日取得・通知生成を持つ。
認証・ユーザー管理の機能群として、ログイン・パスワード変更・ユーザー管理・初期管理者作成の処理を持つ。
備品管理の機能群として、備品の検索・取得・登録・編集・分類一覧・予約状況・CSV一括登録の処理を持つ。
貸出申請・承認の機能群として、貸出申請・申請取消・申請承認・申請却下・申請管理者取消・申請一覧・自分の申請取得の処理を持つ。
貸出・返却・履歴の機能群として、貸出・返却・貸出履歴検索・貸出履歴CSV出力・期限超過一覧取得の処理を持つ。

設計書：設計書/サーバー処理（main）/共通/、設計書/サーバー処理（main）/認証・ユーザー管理/、
設計書/サーバー処理（main）/備品管理/、
設計書/サーバー処理（main）/貸出申請・承認/、
設計書/サーバー処理（main）/貸出・返却・履歴/
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
    LoanHistoryFilter,
    LoanHistoryQuery,
    LoanHistoryResponse,
    LoanRequestAdminCancelRequest,
    LoanRequestCreateRequest,
    LoanRequestListQuery,
    LoanRequestRejectRequest,
    LoanRequestResponse,
    LoanReturnRequest,
    LoginRequest,
    MeResponse,
    Page,
    PageQuery,
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

_LOAN_REQUEST_NOT_FOUND_ERROR = "申請が見つかりません"
_START_DATE_PAST_ERROR = "開始日は今日以降を指定してください"
_DUE_DATE_BEFORE_START_ERROR = "返却予定日は開始日以降を指定してください"
_APPLY_OVERLAP_ERROR = "指定した期間に承認済み・貸出中の予約があります"
_OWN_CANCEL_STATUS_ERROR = "申請中・承認済みの申請のみ取り消せます"
_APPROVE_STATUS_ERROR = "申請中の申請のみ承認できます"
_APPROVE_START_PASSED_ERROR = "開始日を過ぎた申請は承認できません"
_APPROVE_OVERLAP_ERROR = "承認済み・貸出中の予約と期間が重複しているため承認できません"
_REJECT_STATUS_ERROR = "申請中の申請のみ却下できます"
_ADMIN_CANCEL_STATUS_ERROR = "承認済みの申請のみ管理者取消できます"

_LEND_STATUS_ERROR = "承認済みの申請のみ貸出処理できます"
_LEND_BEFORE_START_ERROR = "開始日前の申請は貸出処理できません"
_LEND_AFTER_DUE_ERROR = "返却予定日を過ぎた申請は貸出処理できません"
_LEND_INACTIVE_EQUIPMENT_ERROR = "無効化された備品は貸出処理できません"
_LEND_ALREADY_LENT_ERROR = "この備品は貸出中の別の申請があるため貸出処理できません（先に返却処理を行ってください）"
_LEND_CONFLICT_ERROR = "この備品は既に貸出中です"
_RETURN_STATUS_ERROR = "貸出中の申請のみ返却処理できます"
_LENT_DATE_RANGE_ERROR = "貸出日終了は貸出日開始以降を指定してください"
_LOAN_HISTORY_CSV_MAX_ROWS = 10000
_LOAN_HISTORY_CSV_LIMIT_ERROR = "出力対象が上限（10,000件）を超えています。絞り込み条件を指定してください"
_LOAN_HISTORY_CSV_HEADER = (
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
)
_LOAN_STATUS_LABELS = {LoanStatus.LENT.value: "貸出中", LoanStatus.RETURNED.value: "返却済み"}
_CSV_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

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


# ---- 貸出申請・承認 ----


def _to_loan_request_response(
    loan_request: LoanRequest,
    equipment: Equipment,
    requester: User,
    today: date,
) -> LoanRequestResponse:
    """
    申請レスポンス変換（共通の内部処理）

    貸出申請・備品・申請者から、申請レスポンスを作る。
    期限超過は「状態が貸出中かつ返却予定日が今日（JST）より前」の場合に真とし、期限超過日数は今日と返却予定日の差（日数）とする。
    """
    is_overdue = loan_request.status == LoanStatus.LENT.value and loan_request.due_date < today
    overdue_days = 0
    if is_overdue:
        overdue_period = today - loan_request.due_date
        overdue_days = overdue_period.days
    loan_request_response = LoanRequestResponse(
        id=loan_request.id,
        equipment_id=equipment.id,
        equipment_asset_number=equipment.asset_number,
        equipment_name=equipment.name,
        requester_id=requester.id,
        requester_name=requester.name,
        requester_department=requester.department,
        start_date=loan_request.start_date,
        due_date=loan_request.due_date,
        purpose=loan_request.purpose,
        status=loan_request.status,  # type: ignore[arg-type]
        reason=loan_request.reason,
        return_note=loan_request.return_note,
        requested_at=loan_request.requested_at,
        decided_at=loan_request.decided_at,
        lent_at=loan_request.lent_at,
        returned_at=loan_request.returned_at,
        canceled_at=loan_request.canceled_at,
        is_overdue=is_overdue,
        overdue_days=overdue_days,
    )
    return loan_request_response


def _build_loan_request_response(db: Session, loan_request_id: int, today: date) -> LoanRequestResponse:
    """
    申請レスポンス生成（共通の内部処理）

    貸出申請の詳細（備品・申請者を含む）を取得し、申請レスポンスへ変換する。
    直前の処理で存在を確認済みのため、取得できない場合は404（申請が見つかりません）とする。
    """
    detail = crud.get_loan_request_detail(db, loan_request_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)
    loan_request, equipment, requester = detail
    loan_request_response = _to_loan_request_response(loan_request, equipment, requester, today)
    return loan_request_response


def apply_loan_request(
    db: Session,
    authenticated_user: AuthenticatedUser,
    request: LoanRequestCreateRequest,
) -> LoanRequestResponse:
    """
    貸出申請処理

    設計書：設計書/サーバー処理（main）/貸出申請・承認/貸出申請

    【処理概要】
    - 認証済みユーザーが、備品と貸出期間（開始日・返却予定日）・用途を指定して貸出を申請する。将来日の予約を含む。
    - 期間を検証し、備品を行ロックしたうえで承認済み・貸出中の予約との期間重複を検証して、申請中の申請を登録する。
    - 有効な全管理者（申請者を除く）へ新規申請の通知を生成する。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（申請者）
    - request (LoanRequestCreateRequest) : 貸出申請リクエスト

    【戻り値】
    - loan_request_response (LoanRequestResponse) : 登録した申請

    【例外処理】
    - HTTPException(400) : 開始日が今日より前、または返却予定日が開始日より前の場合
    - HTTPException(404) : 備品が存在しない、または無効化済みの場合
    - HTTPException(409) : 承認済み・貸出中の予約と期間が重複する場合

    【処理フロー】
    1. 現在日時・今日の日付（JST）の取得
    2. 貸出期間の検証
    3. 備品の行ロック取得（二重貸出防止）
    4. 承認済み・貸出中の予約との期間重複の検証
    5. 貸出申請の登録
    6. 有効な全管理者（申請者を除く）への新規申請の通知生成
    7. レスポンス生成用の貸出申請詳細の取得
    8. コミット
    9. 戻り値を設定
    """
    # 1. 現在日時・今日の日付の取得
    now = get_now()
    today = get_today()

    # 2. 貸出期間の検証（貸出期間・予約可能な先の日付・同時申請件数に上限は設けない）
    if request.start_date < today:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_START_DATE_PAST_ERROR)
    if request.due_date < request.start_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_DUE_DATE_BEFORE_START_ERROR)

    # 3. 備品の行ロック取得
    equipment = crud.get_equipment_by_id(db, request.equipment_id, True)
    if equipment is None or not equipment.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_EQUIPMENT_NOT_FOUND_ERROR)

    # 4. 承認済み・貸出中の予約との期間重複の検証（申請中同士の重複は許可する。先に承認された方が有効）
    overlap_count = crud.count_overlapping_reservations(
        db,
        request.equipment_id,
        request.start_date,
        request.due_date,
        today,
        None,
    )
    if overlap_count >= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_APPLY_OVERLAP_ERROR)

    # 5. 貸出申請の登録
    loan_request = crud.create_loan_request(
        db,
        request.equipment_id,
        authenticated_user.id,
        request.start_date,
        request.due_date,
        request.purpose,
        now,
    )

    # 6. 有効な全管理者（申請者自身を除く）へ新規申請の通知を生成
    active_admin_ids = crud.get_active_admin_ids(db)
    recipient_ids = [admin_id for admin_id in active_admin_ids if admin_id != authenticated_user.id]
    if recipient_ids:
        create_notifications(db, recipient_ids, NotificationType.NEW_REQUEST.value, loan_request.id, today)

    # 7. レスポンス生成用の貸出申請詳細の取得
    loan_request_response = _build_loan_request_response(db, loan_request.id, today)

    # 8. コミット
    db.commit()

    # 9. 戻り値を設定
    return loan_request_response


def cancel_own_loan_request(
    db: Session,
    authenticated_user: AuthenticatedUser,
    loan_request_id: int,
) -> LoanRequestResponse:
    """
    申請取消処理

    設計書：設計書/サーバー処理（main）/貸出申請・承認/申請取消

    【処理概要】
    - 認証済みユーザーが、自分の「申請中」または「承認済み」の申請を、貸出前に取り消す。
    - 本人による取消のため通知は生成しない。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー
    - loan_request_id (int) : 貸出申請内部ID

    【戻り値】
    - loan_request_response (LoanRequestResponse) : 取消後の申請

    【例外処理】
    - HTTPException(404) : 申請が存在しない、または他人の申請の場合（存在有無を判別できないよう同じ応答にする）
    - HTTPException(400) : 申請中・承認済み以外の状態の場合

    【処理フロー】
    1. 現在日時の取得
    2. 貸出申請の行ロック取得と本人確認
    3. 取消可能な状態であることの検証（ロック取得後の最新の状態で判定する）
    4. 貸出申請を取消へ更新（理由は空文字）
    5. レスポンス生成用の貸出申請詳細の取得（今日の取得を含む）
    6. コミット
    7. 戻り値を設定
    """
    # 1. 現在日時の取得
    now = get_now()

    # 2. 貸出申請の行ロック取得と本人確認
    loan_request = crud.get_loan_request_by_id(db, loan_request_id, True)
    if loan_request is None or loan_request.requester_id != authenticated_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)

    # 3. 取消可能な状態であることの検証
    cancelable_statuses = [LoanStatus.REQUESTED.value, LoanStatus.APPROVED.value]
    if loan_request.status not in cancelable_statuses:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_OWN_CANCEL_STATUS_ERROR)

    # 4. 貸出申請を取消へ更新
    crud.cancel_loan_request(db, loan_request, authenticated_user.id, "", now)

    # 5. レスポンス生成用の貸出申請詳細の取得
    today = get_today()
    loan_request_response = _build_loan_request_response(db, loan_request_id, today)

    # 6. コミット
    db.commit()

    # 7. 戻り値を設定
    return loan_request_response


def approve_loan_request(
    db: Session,
    authenticated_user: AuthenticatedUser,
    loan_request_id: int,
) -> LoanRequestResponse:
    """
    申請承認処理

    設計書：設計書/サーバー処理（main）/貸出申請・承認/申請承認

    【処理概要】
    - 管理者が、申請中の申請を承認する。承認時に期間の重複を再検証する（二重貸出の防止）。
    - 備品を行ロックしたうえで、申請の状態・開始日・承認済み・貸出中の予約との期間重複を検証して承認済みへ更新し、
      申請者へ承認の通知を生成する。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）
    - loan_request_id (int) : 貸出申請内部ID

    【戻り値】
    - loan_request_response (LoanRequestResponse) : 承認後の申請

    【例外処理】
    - HTTPException(404) : 申請が存在しない場合
    - HTTPException(400) : 申請中でない、または開始日を過ぎている場合
    - HTTPException(409) : 承認済み・貸出中の予約と期間が重複する場合（排他制約違反を含む）

    【処理フロー】
    1. 現在日時・今日の日付（JST）の取得
    2. 対象の備品を特定するための貸出申請の取得（ロックなし）
    3. 備品の行ロック取得（ロック順は「備品 → 貸出申請」に固定する）
    4. 貸出申請の行ロック再取得（ロック取得後の最新の状態を得る）
    5. 承認可能であることの検証
    6. 承認済み・貸出中の予約との期間重複の再検証（自分自身を除く）
    7. 貸出申請を承認済みへ更新（排他制約違反は409）
    8. 申請者へ承認の通知を生成
    9. レスポンス生成用の貸出申請詳細の取得
    10. コミット（排他制約違反は409）
    11. 戻り値を設定
    """
    # 1. 現在日時・今日の日付の取得
    now = get_now()
    today = get_today()

    # 2. 対象の備品を特定するための貸出申請の取得（ロックなし）
    unlocked_loan_request = crud.get_loan_request_by_id(db, loan_request_id, False)
    if unlocked_loan_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)

    # 3. 備品の行ロック取得（二重貸出防止。ロックの取得順は「備品 → 貸出申請」）
    crud.get_equipment_by_id(db, unlocked_loan_request.equipment_id, True)

    # 4. 貸出申請の行ロック再取得
    loan_request = crud.get_loan_request_by_id(db, loan_request_id, True)
    if loan_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)

    # 5. 承認可能であることの検証
    if loan_request.status != LoanStatus.REQUESTED.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_APPROVE_STATUS_ERROR)
    if loan_request.start_date < today:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_APPROVE_START_PASSED_ERROR)

    # 6. 承認済み・貸出中の予約との期間重複の再検証
    overlap_count = crud.count_overlapping_reservations(
        db,
        loan_request.equipment_id,
        loan_request.start_date,
        loan_request.due_date,
        today,
        loan_request.id,
    )
    if overlap_count >= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_APPROVE_OVERLAP_ERROR)

    # 7. 貸出申請を承認済みへ更新（判定後の割り込みは、DBの排他制約による最終防衛で検出する）
    try:
        crud.decide_loan_request(db, loan_request, LoanStatus.APPROVED.value, authenticated_user.id, "", now)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_APPROVE_OVERLAP_ERROR) from None

    # 8. 申請者へ承認の通知を生成
    create_notifications(db, [loan_request.requester_id], NotificationType.APPROVED.value, loan_request.id, today)

    # 9. レスポンス生成用の貸出申請詳細の取得
    loan_request_response = _build_loan_request_response(db, loan_request_id, today)

    # 10. コミット
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_APPROVE_OVERLAP_ERROR) from None

    # 11. 戻り値を設定
    return loan_request_response


def reject_loan_request(
    db: Session,
    authenticated_user: AuthenticatedUser,
    loan_request_id: int,
    request: LoanRequestRejectRequest,
) -> LoanRequestResponse:
    """
    申請却下処理

    設計書：設計書/サーバー処理（main）/貸出申請・承認/申請却下

    【処理概要】
    - 管理者が、申請中の申請を、理由を付けて却下する。
    - 却下は備品の期間に影響しないため、備品の行ロックは行わない（貸出申請のみをロックする）。
    - 申請者へ却下の通知を生成する。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）
    - loan_request_id (int) : 貸出申請内部ID
    - request (LoanRequestRejectRequest) : 申請却下リクエスト

    【戻り値】
    - loan_request_response (LoanRequestResponse) : 却下後の申請

    【例外処理】
    - HTTPException(404) : 申請が存在しない場合
    - HTTPException(400) : 申請中でない場合

    【処理フロー】
    1. 現在日時・今日の日付（JST）の取得
    2. 貸出申請の行ロック取得と状態の検証
    3. 貸出申請を却下へ更新
    4. 申請者へ却下の通知を生成
    5. レスポンス生成用の貸出申請詳細の取得
    6. コミット
    7. 戻り値を設定
    """
    # 1. 現在日時・今日の日付の取得
    now = get_now()
    today = get_today()

    # 2. 貸出申請の行ロック取得と状態の検証
    loan_request = crud.get_loan_request_by_id(db, loan_request_id, True)
    if loan_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)
    if loan_request.status != LoanStatus.REQUESTED.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_REJECT_STATUS_ERROR)

    # 3. 貸出申請を却下へ更新
    crud.decide_loan_request(db, loan_request, LoanStatus.REJECTED.value, authenticated_user.id, request.reason, now)

    # 4. 申請者へ却下の通知を生成
    create_notifications(db, [loan_request.requester_id], NotificationType.REJECTED.value, loan_request.id, today)

    # 5. レスポンス生成用の貸出申請詳細の取得
    loan_request_response = _build_loan_request_response(db, loan_request_id, today)

    # 6. コミット
    db.commit()

    # 7. 戻り値を設定
    return loan_request_response


def admin_cancel_loan_request(
    db: Session,
    authenticated_user: AuthenticatedUser,
    loan_request_id: int,
    request: LoanRequestAdminCancelRequest,
) -> LoanRequestResponse:
    """
    申請管理者取消処理

    設計書：設計書/サーバー処理（main）/貸出申請・承認/申請管理者取消

    【処理概要】
    - 管理者が、承認済みの申請を、理由を付けて取り消す（備品故障等）。
    - 申請者へ取消の通知を生成する。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）
    - loan_request_id (int) : 貸出申請内部ID
    - request (LoanRequestAdminCancelRequest) : 申請管理者取消リクエスト

    【戻り値】
    - loan_request_response (LoanRequestResponse) : 取消後の申請

    【例外処理】
    - HTTPException(404) : 申請が存在しない場合
    - HTTPException(400) : 承認済みでない場合

    【処理フロー】
    1. 現在日時・今日の日付（JST）の取得
    2. 貸出申請の行ロック取得と状態の検証（ロック取得後の最新の状態で判定する）
    3. 貸出申請を取消へ更新
    4. 申請者へ取消の通知を生成
    5. レスポンス生成用の貸出申請詳細の取得
    6. コミット
    7. 戻り値を設定
    """
    # 1. 現在日時・今日の日付の取得
    now = get_now()
    today = get_today()

    # 2. 貸出申請の行ロック取得と状態の検証
    loan_request = crud.get_loan_request_by_id(db, loan_request_id, True)
    if loan_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)
    if loan_request.status != LoanStatus.APPROVED.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_ADMIN_CANCEL_STATUS_ERROR)

    # 3. 貸出申請を取消へ更新
    crud.cancel_loan_request(db, loan_request, authenticated_user.id, request.reason, now)

    # 4. 申請者へ取消の通知を生成
    create_notifications(db, [loan_request.requester_id], NotificationType.CANCELED.value, loan_request.id, today)

    # 5. レスポンス生成用の貸出申請詳細の取得
    loan_request_response = _build_loan_request_response(db, loan_request_id, today)

    # 6. コミット
    db.commit()

    # 7. 戻り値を設定
    return loan_request_response


def _build_loan_request_page(
    items: list[tuple[LoanRequest, Equipment, User]],
    total: int,
    query: LoanRequestListQuery,
    today: date,
) -> Page[LoanRequestResponse]:
    """
    申請一覧レスポンス生成（共通の内部処理）

    貸出申請の一覧・総件数から、ページ形式の申請レスポンスを作る。
    """
    responses = [
        _to_loan_request_response(loan_request, equipment, requester, today)
        for loan_request, equipment, requester in items
    ]
    page_response = Page[LoanRequestResponse](
        items=responses,
        total=total,
        page=query.page,
        page_size=query.page_size,
    )
    return page_response


def list_loan_requests_admin(db: Session, query: LoanRequestListQuery) -> Page[LoanRequestResponse]:
    """
    承認待ち申請一覧取得処理

    設計書：設計書/サーバー処理（main）/貸出申請・承認/承認待ち申請一覧取得

    【処理概要】
    - 管理者が、承認待ち（既定）を中心に、全申請を状態で絞り込んで確認する。
    - 状態の省略時は申請中とし、申請日時の古い順（承認待ちを古い順に処理できる）で返却する。

    【パラメータ】
    - db (Session) : DBセッション
    - query (LoanRequestListQuery) : 貸出申請一覧クエリ

    【戻り値】
    - page_response (Page[LoanRequestResponse]) : ページ形式の申請一覧

    【例外処理】
    - なし

    【処理フロー】
    1. 今日の日付（JST）の取得
    2. 貸出申請の一覧・総件数の取得（申請者では絞り込まない。状態の省略時は申請中）
    3. 戻り値を設定
    """
    # 1. 今日の日付の取得
    today = get_today()

    # 2. 貸出申請の一覧・総件数の取得
    target_status = query.status
    if target_status is None:
        target_status = LoanStatus.REQUESTED.value
    items, total = crud.get_loan_requests(db, None, target_status, False, query.page, query.page_size)

    # 3. 戻り値を設定
    page_response = _build_loan_request_page(items, total, query, today)
    return page_response


def list_my_loan_requests(
    db: Session,
    authenticated_user: AuthenticatedUser,
    query: LoanRequestListQuery,
) -> Page[LoanRequestResponse]:
    """
    自分の申請一覧取得処理

    設計書：設計書/サーバー処理（main）/貸出申請・承認/自分の申請一覧取得

    【処理概要】
    - 認証済みユーザーが、自分の申請の状態と履歴を一覧で確認する。
    - 自分の申請のみを、状態で絞り込み（省略時は絞り込まない）、申請日時の新しい順で返却する。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー
    - query (LoanRequestListQuery) : 貸出申請一覧クエリ

    【戻り値】
    - page_response (Page[LoanRequestResponse]) : ページ形式の申請一覧

    【例外処理】
    - なし

    【処理フロー】
    1. 今日の日付（JST）の取得
    2. 自分の貸出申請の一覧・総件数の取得
    3. 戻り値を設定
    """
    # 1. 今日の日付の取得
    today = get_today()

    # 2. 自分の貸出申請の一覧・総件数の取得
    items, total = crud.get_loan_requests(db, authenticated_user.id, query.status, True, query.page, query.page_size)

    # 3. 戻り値を設定
    page_response = _build_loan_request_page(items, total, query, today)
    return page_response


def get_own_loan_request(
    db: Session,
    authenticated_user: AuthenticatedUser,
    loan_request_id: int,
) -> LoanRequestResponse:
    """
    自分の申請取得処理

    設計書：設計書/サーバー処理（main）/貸出申請・承認/自分の申請取得

    【処理概要】
    - 認証済みユーザーが、自分の申請を1件確認する。
    - 他人の申請は、存在有無を判別できないよう存在しない場合と同じ応答（404）にする。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー
    - loan_request_id (int) : 貸出申請内部ID

    【戻り値】
    - loan_request_response (LoanRequestResponse) : 申請

    【例外処理】
    - HTTPException(404) : 申請が存在しない、または他人の申請の場合

    【処理フロー】
    1. 貸出申請の詳細の取得と本人確認
    2. 今日の日付（JST）の取得
    3. 戻り値を設定
    """
    # 1. 貸出申請の詳細の取得と本人確認
    detail = crud.get_loan_request_detail(db, loan_request_id)
    if detail is None or detail[0].requester_id != authenticated_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)
    loan_request, equipment, requester = detail

    # 2. 今日の日付の取得
    today = get_today()

    # 3. 戻り値を設定
    loan_request_response = _to_loan_request_response(loan_request, equipment, requester, today)
    return loan_request_response


# ---- 貸出・返却・履歴 ----


def lend_loan_request(
    db: Session,
    authenticated_user: AuthenticatedUser,
    loan_request_id: int,
) -> LoanRequestResponse:
    """
    貸出実行処理

    設計書：設計書/サーバー処理（main）/貸出・返却・履歴/貸出処理

    【処理概要】
    - 管理者が、承認済みの申請について、備品を借用者へ渡したことを記録する（貸出中へ更新する）。
    - 備品を行ロックしたうえで貸出申請を行ロックして最新の状態を得て、承認済み・開始日以降・返却予定日以前・備品の有効・
      貸出中の別申請なしを検証し、貸出中へ更新する。貸出処理では通知を生成しない。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）
    - loan_request_id (int) : 貸出申請内部ID

    【戻り値】
    - loan_request_response (LoanRequestResponse) : 貸出後の申請

    【例外処理】
    - HTTPException(404) : 申請が存在しない場合
    - HTTPException(400) : 承認済みでない、開始日前、返却予定日を過ぎている、備品が無効、貸出中の別申請がある場合
    - HTTPException(409) : 貸出中の一意制約に違反した場合（判定後の割り込み）

    【処理フロー】
    1. 現在日時・今日の日付（JST）の取得
    2. 対象の備品を特定するための貸出申請の取得（ロックなし）
    3. 備品の行ロック取得（ロック順は「備品 → 貸出申請」に固定する）
    4. 貸出申請の行ロック再取得（ロック取得後の最新の状態を得る）
    5. 貸出可能であることの検証
    6. 同一備品に貸出中の別申請がないことの検証
    7. 貸出申請を貸出中へ更新（一意制約違反は409）
    8. レスポンス生成用の貸出申請詳細の取得
    9. コミット（一意制約違反は409）
    10. 戻り値を設定
    """
    # 1. 現在日時・今日の日付の取得
    now = get_now()
    today = get_today()

    # 2. 対象の備品を特定するための貸出申請の取得（ロックなし）
    unlocked_loan_request = crud.get_loan_request_by_id(db, loan_request_id, False)
    if unlocked_loan_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)

    # 3. 備品の行ロック取得（二重貸出防止）
    equipment = crud.get_equipment_by_id(db, unlocked_loan_request.equipment_id, True)

    # 4. 貸出申請の行ロック再取得
    loan_request = crud.get_loan_request_by_id(db, loan_request_id, True)
    if loan_request is None or equipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)

    # 5. 貸出可能であることの検証
    if loan_request.status != LoanStatus.APPROVED.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_LEND_STATUS_ERROR)
    if loan_request.start_date > today:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_LEND_BEFORE_START_ERROR)
    if loan_request.due_date < today:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_LEND_AFTER_DUE_ERROR)
    if not equipment.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_LEND_INACTIVE_EQUIPMENT_ERROR)

    # 6. 同一備品に貸出中の別申請がないことの検証（期限超過中で未返却のものを含む）
    lent_loan = crud.get_lent_loan_by_equipment(db, loan_request.equipment_id)
    if lent_loan is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_LEND_ALREADY_LENT_ERROR)

    # 7. 貸出申請を貸出中へ更新（判定後の割り込みは、DBの一意制約による最終防衛で検出する）
    try:
        crud.lend_loan_request(db, loan_request, authenticated_user.id, now)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_LEND_CONFLICT_ERROR) from None

    # 8. レスポンス生成用の貸出申請詳細の取得
    loan_request_response = _build_loan_request_response(db, loan_request_id, today)

    # 9. コミット
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_LEND_CONFLICT_ERROR) from None

    # 10. 戻り値を設定
    return loan_request_response


def return_loan_request(
    db: Session,
    authenticated_user: AuthenticatedUser,
    loan_request_id: int,
    request: LoanReturnRequest,
) -> LoanRequestResponse:
    """
    返却実行処理

    設計書：設計書/サーバー処理（main）/貸出・返却・履歴/返却処理

    【処理概要】
    - 管理者が、貸出中の申請について、備品が返却されたことを記録する（返却済みへ更新する）。
    - 返却は備品の占有を解放する更新のため、備品の行ロックは行わず貸出申請のみをロックする。
    - 返却日時はサーバー時刻を記録する。返却予定日を過ぎていても通常どおり記録し、通知は生成しない。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者）
    - loan_request_id (int) : 貸出申請内部ID
    - request (LoanReturnRequest) : 返却リクエスト（返却時状態メモ）

    【戻り値】
    - loan_request_response (LoanRequestResponse) : 返却後の申請

    【例外処理】
    - HTTPException(404) : 申請が存在しない場合
    - HTTPException(400) : 貸出中でない場合（二重の返却操作は2回目が該当する）

    【処理フロー】
    1. 現在日時・今日の日付（JST）の取得
    2. 貸出申請の行ロック取得と状態の検証
    3. 貸出申請を返却済みへ更新
    4. レスポンス生成用の貸出申請詳細の取得
    5. コミット
    6. 戻り値を設定
    """
    # 1. 現在日時・今日の日付の取得
    now = get_now()
    today = get_today()

    # 2. 貸出申請の行ロック取得と状態の検証
    loan_request = crud.get_loan_request_by_id(db, loan_request_id, True)
    if loan_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_LOAN_REQUEST_NOT_FOUND_ERROR)
    if loan_request.status != LoanStatus.LENT.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_RETURN_STATUS_ERROR)

    # 3. 貸出申請を返却済みへ更新
    crud.return_loan_request(db, loan_request, authenticated_user.id, request.return_note, now)

    # 4. レスポンス生成用の貸出申請詳細の取得
    loan_request_response = _build_loan_request_response(db, loan_request_id, today)

    # 5. コミット
    db.commit()

    # 6. 戻り値を設定
    return loan_request_response


def _to_lent_datetime_range(filter_condition: LoanHistoryFilter) -> tuple[datetime | None, datetime | None]:
    """
    貸出日範囲変換（共通の内部処理）

    貸出日の範囲（JSTの暦日）を検証し、DB検索用の日時範囲（UTC）へ変換する。
    貸出日開始はその日のJST 0時、貸出日終了は終了日を含めるため翌日のJST 0時（「より前」で判定する）とする。
    貸出日終了が貸出日開始より前の場合は400とする。
    """
    from_date = filter_condition.from_date
    to_date = filter_condition.to_date
    if from_date is not None and to_date is not None and to_date < from_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_LENT_DATE_RANGE_ERROR)
    day_start_time = datetime.min.time()
    lent_from = None
    if from_date is not None:
        from_start = datetime.combine(from_date, day_start_time, tzinfo=JST)
        lent_from = from_start.astimezone(UTC)
    lent_to = None
    if to_date is not None:
        next_day = to_date + timedelta(days=1)
        to_end = datetime.combine(next_day, day_start_time, tzinfo=JST)
        lent_to = to_end.astimezone(UTC)
    return lent_from, lent_to


def _calculate_delay_days(loan_request: LoanRequest, today: date) -> int:
    """
    遅延日数算出（共通の内部処理）

    貸出中は「今日（JST）− 返却予定日」、返却済みは「返却日時をJSTへ変換した日付 − 返却予定日」とし、負の場合は0とする。
    """
    end_date = today
    if loan_request.returned_at is not None:
        returned_at_jst = loan_request.returned_at.astimezone(JST)
        end_date = returned_at_jst.date()
    delay_period = end_date - loan_request.due_date
    delay_days = max(delay_period.days, 0)
    return delay_days


def _to_loan_history_response(
    loan_request: LoanRequest,
    equipment: Equipment,
    requester: User,
    today: date,
) -> LoanHistoryResponse:
    """
    貸出履歴レスポンス変換（共通の内部処理）

    貸出申請・備品・借用者から、貸出履歴レスポンスを作る。貸出日時は貸出履歴の条件上、必ず存在する。
    """
    delay_days = _calculate_delay_days(loan_request, today)
    lent_at = loan_request.lent_at
    if lent_at is None:
        raise ValueError("貸出履歴に貸出日時がありません")
    loan_history_response = LoanHistoryResponse(
        id=loan_request.id,
        equipment_id=equipment.id,
        equipment_asset_number=equipment.asset_number,
        equipment_name=equipment.name,
        requester_id=requester.id,
        requester_name=requester.name,
        requester_department=requester.department,
        start_date=loan_request.start_date,
        due_date=loan_request.due_date,
        purpose=loan_request.purpose,
        status=loan_request.status,  # type: ignore[arg-type]
        lent_at=lent_at,
        returned_at=loan_request.returned_at,
        return_note=loan_request.return_note,
        delay_days=delay_days,
    )
    return loan_history_response


def search_loan_history(db: Session, query: LoanHistoryQuery) -> Page[LoanHistoryResponse]:
    """
    貸出履歴検索処理

    設計書：設計書/サーバー処理（main）/貸出・返却・履歴/貸出履歴検索

    【処理概要】
    - 管理者が、過去の貸出（貸出中・返却済み）を、期間・備品・借用者で絞り込んで確認する（遅延日数も確認できる）。
    - 貸出日の範囲を検証・変換し、条件に合う貸出履歴を貸出日時の新しい順でページ単位に返却する。

    【パラメータ】
    - db (Session) : DBセッション
    - query (LoanHistoryQuery) : 貸出履歴クエリ

    【戻り値】
    - loan_history_page (Page[LoanHistoryResponse]) : ページ形式の貸出履歴

    【例外処理】
    - HTTPException(400) : 貸出日終了が貸出日開始より前の場合

    【処理フロー】
    1. 今日の日付（JST）の取得
    2. 貸出日の範囲の検証と、DB検索用の日時範囲への変換
    3. 貸出履歴の一覧・総件数の取得（存在しない備品・借用者の指定は該当なしとする）
    4. 戻り値を設定
    """
    # 1. 今日の日付の取得
    today = get_today()

    # 2. 貸出日の範囲の検証と変換
    lent_from, lent_to = _to_lent_datetime_range(query)

    # 3. 貸出履歴の一覧・総件数の取得
    items, total = crud.search_loan_history(
        db, lent_from, lent_to, query.equipment_id, query.requester_id, query.page, query.page_size
    )

    # 4. 戻り値を設定
    responses = [
        _to_loan_history_response(loan_request, equipment, requester, today)
        for loan_request, equipment, requester in items
    ]
    loan_history_page = Page[LoanHistoryResponse](
        items=responses,
        total=total,
        page=query.page,
        page_size=query.page_size,
    )
    return loan_history_page


def _sanitize_csv_cell(value: str) -> str:
    """
    CSVセル無害化（共通の内部処理）

    CSVインジェクション対策として、文字列のセルの先頭が`=`・`+`・`-`・`@`・タブ・復帰のいずれかの場合、
    先頭に`'`を付与する（Excel等で数式として実行されないようにする）。それ以外はそのまま返す。
    """
    if value.startswith(_CSV_DANGEROUS_PREFIXES):
        return "'" + value
    return value


def export_loan_history_csv(
    db: Session,
    authenticated_user: AuthenticatedUser,
    filter_condition: LoanHistoryFilter,
) -> tuple[bytes, str]:
    """
    貸出履歴CSV作成処理

    設計書：設計書/サーバー処理（main）/貸出・返却・履歴/貸出履歴CSV出力

    【処理概要】
    - 管理者が、貸出履歴を条件で絞り込んでCSV（UTF-8・BOM付き）として出力する。ページングはしない。
    - 出力件数の上限（10,000件）を超える場合はエラーとする。文字列のセルはCSVインジェクション対策で無害化する。

    【パラメータ】
    - db (Session) : DBセッション
    - authenticated_user (AuthenticatedUser) : 認証済みユーザー（有効な管理者。出力の記録に用いる）
    - filter_condition (LoanHistoryFilter) : 貸出履歴条件

    【戻り値】
    - csv_content_and_filename (tuple[bytes, str]) : （CSVの内容（UTF-8・BOM付き）, 出力ファイル名）

    【例外処理】
    - HTTPException(400) : 貸出日終了が貸出日開始より前の場合、出力対象が上限を超える場合

    【処理フロー】
    1. 今日の日付（JST）の取得
    2. 貸出日の範囲の検証と、DB検索用の日時範囲への変換
    3. 出力対象の貸出履歴を上限件数＋1件まで取得し、上限超過を検証
    4. CSVの内容の作成（見出し行と各履歴の行。文字列のセルは無害化する）
    5. 出力ファイル名の作成（サーバーが固定の書式で作成し、リクエストの値は含めない）
    6. 出力の実行をログへ記録（ユーザー内部ID・エンドポイント・出力件数のみ）
    7. 戻り値を設定
    """
    # 1. 今日の日付の取得
    today = get_today()

    # 2. 貸出日の範囲の検証と変換
    lent_from, lent_to = _to_lent_datetime_range(filter_condition)

    # 3. 出力対象の取得と上限超過の検証（超過の検知のため1件多く取得する）
    fetch_limit = _LOAN_HISTORY_CSV_MAX_ROWS + 1
    rows = crud.get_loan_history_for_export(
        db, lent_from, lent_to, filter_condition.equipment_id, filter_condition.requester_id, fetch_limit
    )
    row_count = len(rows)
    if row_count > _LOAN_HISTORY_CSV_MAX_ROWS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_LOAN_HISTORY_CSV_LIMIT_ERROR)

    # 4. CSVの内容の作成
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_LOAN_HISTORY_CSV_HEADER)
    for loan_request, equipment, requester in rows:
        delay_days = _calculate_delay_days(loan_request, today)
        lent_at = loan_request.lent_at
        lent_at_text = ""
        if lent_at is not None:
            lent_at_jst = lent_at.astimezone(JST)
            lent_at_text = lent_at_jst.strftime("%Y-%m-%d %H:%M:%S")
        returned_at_text = ""
        if loan_request.returned_at is not None:
            returned_at_jst = loan_request.returned_at.astimezone(JST)
            returned_at_text = returned_at_jst.strftime("%Y-%m-%d %H:%M:%S")
        status_text = _LOAN_STATUS_LABELS.get(loan_request.status, loan_request.status)
        row = [
            loan_request.id,
            _sanitize_csv_cell(equipment.asset_number),
            _sanitize_csv_cell(equipment.name),
            _sanitize_csv_cell(requester.name),
            _sanitize_csv_cell(requester.department),
            loan_request.start_date.isoformat(),
            loan_request.due_date.isoformat(),
            _sanitize_csv_cell(loan_request.purpose),
            status_text,
            lent_at_text,
            returned_at_text,
            delay_days,
            _sanitize_csv_cell(loan_request.return_note),
        ]
        writer.writerow(row)
    csv_text = "﻿" + buffer.getvalue()
    csv_content = csv_text.encode("utf-8")

    # 5. 出力ファイル名の作成
    filename = "loan_history_" + today.strftime("%Y%m%d") + ".csv"

    # 6. 出力の実行をログへ記録（個人情報・絞り込みの入力値そのものは記録しない）
    logger.info(
        "貸出履歴CSV出力: ユーザー内部ID=%s エンドポイント=/api/admin/loan-history/export 出力件数=%d",
        authenticated_user.id,
        row_count,
    )

    # 7. 戻り値を設定
    csv_content_and_filename = (csv_content, filename)
    return csv_content_and_filename


def list_overdue_loan_requests(db: Session, query: PageQuery) -> Page[LoanRequestResponse]:
    """
    期限超過一覧取得処理

    設計書：設計書/サーバー処理（main）/貸出・返却・履歴/期限超過一覧取得

    【処理概要】
    - 管理者が、返却予定日を過ぎても返却されていない貸出中の申請を、返却予定日の古い順（遅延の大きい順）で確認する。

    【パラメータ】
    - db (Session) : DBセッション
    - query (PageQuery) : ページング条件

    【戻り値】
    - loan_request_page (Page[LoanRequestResponse]) : ページ形式の申請一覧

    【例外処理】
    - なし

    【処理フロー】
    1. 今日の日付（JST）の取得
    2. 期限超過の貸出中の申請の一覧・総件数の取得
    3. 戻り値を設定
    """
    # 1. 今日の日付の取得
    today = get_today()

    # 2. 期限超過の貸出中の申請の一覧・総件数の取得
    items, total = crud.get_overdue_loan_requests(db, today, query.page, query.page_size)

    # 3. 戻り値を設定
    responses = [
        _to_loan_request_response(loan_request, equipment, requester, today)
        for loan_request, equipment, requester in items
    ]
    loan_request_page = Page[LoanRequestResponse](
        items=responses,
        total=total,
        page=query.page,
        page_size=query.page_size,
    )
    return loan_request_page
