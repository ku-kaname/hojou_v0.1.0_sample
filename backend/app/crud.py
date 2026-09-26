"""
データアクセス層（CRUD）

【概要】
DBへの読み書きだけを担当する。業務判断・commitは行わない（書き込み後は`flush`まで。commitはサービス層が行う）。
本ファイルは機能群ごとの実装に伴い関数が追加される。

設計書：設計書/CRUD/
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Equipment, LoanRequest, LoanStatus, Notification, NotificationType, Role, User

# 日次通知（同日に1件だけ生成する種別）
_DAILY_NOTIFICATION_TYPES = (NotificationType.DUE_SOON.value, NotificationType.OVERDUE.value)


def get_user_by_id(db: Session, user_id: int, for_update: bool = False) -> User | None:
    """
    ユーザー内部ID指定取得

    設計書：設計書/CRUD/認証・ユーザー管理/ユーザー内部ID指定取得

    【処理概要】
    - 内部IDを指定してユーザー情報を1件取得する（有効・無効を問わない）。
    - 認証・認可の共通検証（現在ユーザー取得）から利用されるため、共通の実装に含める。

    【パラメータ】
    - db (Session) : DBセッション
    - user_id (int) : ユーザー内部ID
    - for_update (bool) : 行ロック要否。Trueの場合はFOR UPDATEで行ロックする（省略時False）

    【戻り値】
    - user (User | None) : ユーザー情報（該当なしはNone）

    【例外処理】
    - なし

    【処理フロー】
    1. app_userテーブルから内部IDが一致するレコードを取得（有効フラグでは絞り込まない）
    2. 戻り値を設定
    """
    # 1. ユーザー情報取得
    statement = select(User).where(User.id == user_id)
    if for_update:
        # 再取得時に最新値を読むため、populate_existingで既存の読み込み済み値も更新する
        statement = statement.with_for_update().execution_options(populate_existing=True)
    user = db.execute(statement).scalar_one_or_none()

    # 2. 戻り値を設定
    return user


def create_notification(
    db: Session,
    recipient_id: int,
    notification_type: str,
    loan_request_id: int,
    notified_date: date,
) -> bool:
    """
    通知登録

    設計書：設計書/CRUD/共通/通知登録

    【処理概要】
    - 通知を1件登録する（F08）。日次通知（返却期限）は、同日に登録済みの場合は登録せず読み飛ばす。
    - commitは呼び出し元が行う（本関数はflushまで）。

    【パラメータ】
    - db (Session) : DBセッション
    - recipient_id (int) : 宛先（ユーザー内部ID）
    - notification_type (str) : 種別（通知種別の列挙値）
    - loan_request_id (int) : 関連申請（内部ID）
    - notified_date (date) : 通知日（JSTの暦日）

    【戻り値】
    - is_created (bool) : 登録した場合はTrue、日次通知の重複で読み飛ばした場合はFalse

    【例外処理】
    - なし

    【処理フロー】
    1. 通知レコードの登録（既読フラグはFALSE、作成日時はDBの既定値）
       - 種別が返却期限前・期限超過：INSERT ... ON CONFLICT DO NOTHING（一意インデックスuq_notification_daily）
       - 上記以外：通常のINSERT
    2. 戻り値を設定
    """
    # 1. 通知レコードの登録
    values = {
        "recipient_id": recipient_id,
        "type": notification_type,
        "loan_request_id": loan_request_id,
        "is_read": False,
        "notified_date": notified_date,
    }
    if notification_type in _DAILY_NOTIFICATION_TYPES:
        # 部分一意インデックスに対する重複回避のため、インデックスの条件（WHERE）も合わせて指定する
        daily_statement = (
            pg_insert(Notification)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["recipient_id", "type", "loan_request_id", "notified_date"],
                index_where=Notification.type.in_(_DAILY_NOTIFICATION_TYPES),
            )
            .returning(Notification.id)
        )
        inserted_id = db.execute(daily_statement).scalar_one_or_none()
        # 2. 戻り値を設定
        return inserted_id is not None

    notification = Notification(**values)
    db.add(notification)
    db.flush()

    # 2. 戻り値を設定
    return True


def get_user_by_login_id(db: Session, login_id: str, for_update: bool = False) -> User | None:
    """
    ユーザーID指定取得

    設計書：設計書/CRUD/認証・ユーザー管理/ユーザーID指定取得

    【処理概要】
    - ユーザーID（ログインID）を指定してユーザー情報を1件取得する。
      大文字小文字を区別する完全一致で、有効・無効を問わない。

    【パラメータ】
    - db (Session) : DBセッション
    - login_id (str) : ユーザーID
    - for_update (bool) : 行ロック要否。Trueの場合はFOR UPDATEで行ロックする（省略時False）

    【戻り値】
    - user (User | None) : ユーザー情報（該当なしはNone）

    【例外処理】
    - なし

    【処理フロー】
    1. app_userテーブルからユーザーIDが一致するレコードを取得
    2. 戻り値を設定
    """
    # 1. ユーザー情報取得
    statement = select(User).where(User.login_id == login_id)
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    user = db.execute(statement).scalar_one_or_none()

    # 2. 戻り値を設定
    return user


def create_user(
    db: Session,
    login_id: str,
    name: str,
    department: str,
    password_hash: str,
    role: str,
    must_change_password: bool,
    now: datetime,
) -> User:
    """
    ユーザー作成

    設計書：設計書/CRUD/認証・ユーザー管理/ユーザー作成

    【処理概要】
    - ユーザーを1件登録する。有効フラグは真、トークン世代・連続認証失敗回数は0で登録する。
    - ユーザーIDの重複（一意制約違反）は呼び出し元へ例外（IntegrityError）として伝える。commitは呼び出し元が行う。

    【パラメータ】
    - db (Session) : DBセッション
    - login_id (str) : ユーザーID
    - name (str) : 氏名
    - department (str) : 所属
    - password_hash (str) : ハッシュ済みパスワード
    - role (str) : ロール
    - must_change_password (bool) : 初回パスワード変更要否
    - now (datetime) : 現在日時（作成日時・更新日時に設定）

    【戻り値】
    - user (User) : 登録したユーザー情報

    【例外処理】
    - IntegrityError : ユーザーIDが重複している場合（呼び出し元で扱う）

    【処理フロー】
    1. app_userテーブルへレコードを追加し、flushする
    2. 戻り値を設定
    """
    # 1. ユーザー登録
    user = User(
        login_id=login_id,
        name=name,
        department=department,
        password_hash=password_hash,
        role=role,
        is_active=True,
        must_change_password=must_change_password,
        token_generation=0,
        failed_login_count=0,
        locked_until=None,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()

    # 2. 戻り値を設定
    return user


def update_user_profile(
    db: Session,
    user: User,
    name: str,
    department: str,
    role: str,
    is_active: bool,
    now: datetime,
) -> User:
    """
    ユーザー情報更新

    設計書：設計書/CRUD/認証・ユーザー管理/ユーザー情報更新

    【処理概要】
    - 取得済みのユーザーへ氏名・所属・ロール・有効フラグを反映し、更新日時を設定する。

    【パラメータ】
    - db (Session) : DBセッション
    - user (User) : 更新対象のユーザー（取得済み）
    - name (str) : 氏名
    - department (str) : 所属
    - role (str) : ロール
    - is_active (bool) : 有効フラグ
    - now (datetime) : 現在日時

    【戻り値】
    - user (User) : 更新後のユーザー情報

    【例外処理】
    - なし

    【処理フロー】
    1. ユーザーの各項目と更新日時を設定し、flushする
    2. 戻り値を設定
    """
    # 1. ユーザー情報更新
    user.name = name
    user.department = department
    user.role = role
    user.is_active = is_active
    user.updated_at = now
    db.flush()

    # 2. 戻り値を設定
    return user


def update_password(
    db: Session,
    user: User,
    password_hash: str,
    must_change_password: bool,
    now: datetime,
) -> User:
    """
    パスワード更新

    設計書：設計書/CRUD/認証・ユーザー管理/パスワード更新

    【処理概要】
    - パスワードを更新する。あわせてトークン世代を+1（発行済みトークンの失効）、
      連続認証失敗回数を0、ロック解除日時をNULL（ロック解除）にする。

    【パラメータ】
    - db (Session) : DBセッション
    - user (User) : 更新対象のユーザー（取得済み）
    - password_hash (str) : ハッシュ済みパスワード
    - must_change_password (bool) : 初回パスワード変更要否
    - now (datetime) : 現在日時

    【戻り値】
    - user (User) : 更新後のユーザー情報

    【例外処理】
    - なし

    【処理フロー】
    1. パスワードハッシュ・初回パスワード変更要否・トークン世代・失敗回数・ロック・更新日時を設定し、flushする
    2. 戻り値を設定
    """
    # 1. パスワード更新
    user.password_hash = password_hash
    user.must_change_password = must_change_password
    user.token_generation = user.token_generation + 1
    user.failed_login_count = 0
    user.locked_until = None
    user.updated_at = now
    db.flush()

    # 2. 戻り値を設定
    return user


def increment_token_generation(db: Session, user: User, now: datetime) -> User:
    """
    トークン世代更新

    設計書：設計書/CRUD/認証・ユーザー管理/トークン世代更新

    【処理概要】
    - トークン世代を+1し、発行済みトークンをすべて失効させる。

    【パラメータ】
    - db (Session) : DBセッション
    - user (User) : 更新対象のユーザー（取得済み）
    - now (datetime) : 現在日時

    【戻り値】
    - user (User) : 更新後のユーザー情報

    【例外処理】
    - なし

    【処理フロー】
    1. トークン世代と更新日時を設定し、flushする
    2. 戻り値を設定
    """
    # 1. トークン世代更新
    user.token_generation = user.token_generation + 1
    user.updated_at = now
    db.flush()

    # 2. 戻り値を設定
    return user


def update_login_state(
    db: Session,
    user: User,
    failed_login_count: int,
    locked_until: datetime | None,
    now: datetime,
) -> User:
    """
    ログイン状態更新

    設計書：設計書/CRUD/認証・ユーザー管理/ログイン状態更新

    【処理概要】
    - 連続認証失敗回数とロック解除日時を更新する（ログイン失敗の加算・ロック・解除に使う）。

    【パラメータ】
    - db (Session) : DBセッション
    - user (User) : 更新対象のユーザー（取得済み）
    - failed_login_count (int) : 連続認証失敗回数（0以上）
    - locked_until (datetime | None) : ロック解除日時（ロックしない場合はNone）
    - now (datetime) : 現在日時

    【戻り値】
    - user (User) : 更新後のユーザー情報

    【例外処理】
    - なし

    【処理フロー】
    1. 連続認証失敗回数・ロック解除日時・更新日時を設定し、flushする
    2. 戻り値を設定
    """
    # 1. ログイン状態更新
    user.failed_login_count = failed_login_count
    user.locked_until = locked_until
    user.updated_at = now
    db.flush()

    # 2. 戻り値を設定
    return user


def _escape_like(keyword: str) -> str:
    """LIKE検索のワイルドカード（バックスラッシュ・%・_）を文字として扱うためにエスケープする"""
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped


def get_users(
    db: Session,
    keyword: str | None,
    role: str | None,
    is_active: bool | None,
    page: int,
    page_size: int,
) -> tuple[list[User], int]:
    """
    ユーザー一覧取得

    設計書：設計書/CRUD/認証・ユーザー管理/ユーザー一覧取得

    【処理概要】
    - キーワード・ロール・有効フラグで絞り込み、内部IDの昇順でページ単位にユーザー情報と総件数を取得する。

    【パラメータ】
    - db (Session) : DBセッション
    - keyword (str | None) : ユーザーIDまたは氏名の部分一致（省略時は絞り込まない）
    - role (str | None) : ロール（省略時は絞り込まない）
    - is_active (bool | None) : 有効フラグ（省略時は有効・無効の両方）
    - page (int) : ページ番号（1以上）
    - page_size (int) : 1ページの件数（1〜100）

    【戻り値】
    - users (list[User]) : ユーザー情報一覧（0件は空リスト）
    - total (int) : 総件数
    ※タプル（ユーザー情報一覧, 総件数）で返却する

    【例外処理】
    - なし

    【処理フロー】
    1. 絞り込み条件の作成（キーワードはワイルドカードをエスケープして部分一致）
    2. 総件数の取得
    3. 内部ID昇順での一覧取得（LIMIT/OFFSET）
    4. 戻り値を設定
    """
    # 1. 絞り込み条件の作成
    conditions = []
    if keyword:
        escaped_keyword = _escape_like(keyword)
        like_pattern = "%" + escaped_keyword + "%"
        login_id_condition = User.login_id.ilike(like_pattern, escape="\\")
        name_condition = User.name.ilike(like_pattern, escape="\\")
        conditions.append(or_(login_id_condition, name_condition))
    if role is not None:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active == is_active)

    # 2. 総件数の取得
    count_statement = select(func.count()).select_from(User).where(*conditions)
    total = db.execute(count_statement).scalar_one()

    # 3. 一覧取得
    offset = (page - 1) * page_size
    list_statement = select(User).where(*conditions).order_by(User.id.asc()).limit(page_size).offset(offset)
    list_result = db.execute(list_statement).scalars().all()
    users = list(list_result)

    # 4. 戻り値を設定
    return users, total


def get_active_admin_ids(db: Session) -> list[int]:
    """
    有効管理者ID一覧取得

    設計書：設計書/CRUD/認証・ユーザー管理/有効管理者ID一覧取得

    【処理概要】
    - 有効な管理者の内部ID一覧を、内部IDの昇順で取得する（ロックしない）。

    【パラメータ】
    - db (Session) : DBセッション

    【戻り値】
    - admin_ids (list[int]) : 有効な管理者の内部ID一覧（0件は空リスト）

    【例外処理】
    - なし

    【処理フロー】
    1. app_userテーブルからロールが管理者かつ有効なレコードの内部IDを昇順で取得
    2. 戻り値を設定
    """
    # 1. 有効な管理者の内部ID取得
    statement = select(User.id).where(User.role == Role.ADMIN.value, User.is_active.is_(True)).order_by(User.id.asc())
    admin_id_result = db.execute(statement).scalars().all()
    admin_ids = list(admin_id_result)

    # 2. 戻り値を設定
    return admin_ids


def lock_active_admin_ids(db: Session) -> list[int]:
    """
    有効管理者ロック取得

    設計書：設計書/CRUD/認証・ユーザー管理/有効管理者ロック取得

    【処理概要】
    - 有効な管理者を内部IDの昇順で行ロックし、内部ID一覧を取得する（同時降格・無効化による管理者0人化の防止用）。
    - 並行する管理者更新処理とのデッドロックを避けるため、必ず内部IDの昇順でロックする。

    【パラメータ】
    - db (Session) : DBセッション

    【戻り値】
    - admin_ids (list[int]) : 有効な管理者の内部ID一覧（0件は空リスト）

    【例外処理】
    - なし

    【処理フロー】
    1. app_userテーブルからロールが管理者かつ有効なレコードを、内部IDの昇順でFOR UPDATEロックして内部IDを取得
    2. 戻り値を設定
    """
    # 1. 有効な管理者の行ロック
    statement = (
        select(User.id)
        .where(User.role == Role.ADMIN.value, User.is_active.is_(True))
        .order_by(User.id.asc())
        .with_for_update()
    )
    admin_id_result = db.execute(statement).scalars().all()
    admin_ids = list(admin_id_result)

    # 2. 戻り値を設定
    return admin_ids


def count_lent_by_requester(db: Session, requester_id: int) -> int:
    """
    申請者貸出中件数取得

    設計書：設計書/CRUD/貸出申請・承認/申請者貸出中件数取得

    【処理概要】
    - 申請者を指定して、貸出中の申請の件数を取得する。
    - ユーザー編集（認証・ユーザー管理）から、貸出中の申請があるユーザーの無効化を拒否する判定に使われるため、
      本機能群の実装に含める。

    【パラメータ】
    - db (Session) : DBセッション
    - requester_id (int) : 申請者内部ID

    【戻り値】
    - lent_count (int) : 貸出中件数（0以上）

    【例外処理】
    - なし

    【処理フロー】
    1. loan_requestテーブルから申請者が一致し状態が貸出中の件数を取得
    2. 戻り値を設定
    """
    # 1. 貸出中件数取得
    statement = (
        select(func.count())
        .select_from(LoanRequest)
        .where(LoanRequest.requester_id == requester_id, LoanRequest.status == LoanStatus.LENT.value)
    )
    lent_count = db.execute(statement).scalar_one()

    # 2. 戻り値を設定
    return lent_count


def cancel_pending_by_requester(
    db: Session,
    requester_id: int,
    cancel_reason: str,
    now: datetime,
) -> list[LoanRequest]:
    """
    申請者未貸出申請一括取消

    設計書：設計書/CRUD/貸出申請・承認/申請者未貸出申請一括取消

    【処理概要】
    - 申請者を指定して、未貸出の申請（申請中・承認済み）をすべて取消（システム取消）へ更新する。
    - ユーザー編集（認証・ユーザー管理）のユーザー無効化に伴う自動取消に使われるため、本機能群の実装に含める。
    - commitは呼び出し元が行う（本関数はflushまで）。

    【パラメータ】
    - db (Session) : DBセッション
    - requester_id (int) : 申請者内部ID
    - cancel_reason (str) : 取消理由（1〜200桁）
    - now (datetime) : 現在日時（UTC）

    【戻り値】
    - canceled_loan_requests (list[LoanRequest]) : 取消した貸出申請一覧（該当なしは空リスト）

    【例外処理】
    - なし

    【処理フロー】
    1. 対象の貸出申請を内部IDの昇順で行ロックして取得（デッドロック回避のためロック順を固定）
    2. 各貸出申請を取消へ更新し、flushする（取消した者はNULL＝システム取消）
    3. 戻り値を設定
    """
    # 1. 対象の貸出申請の取得
    pending_statuses = (LoanStatus.REQUESTED.value, LoanStatus.APPROVED.value)
    statement = (
        select(LoanRequest)
        .where(LoanRequest.requester_id == requester_id, LoanRequest.status.in_(pending_statuses))
        .order_by(LoanRequest.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    canceled_result = db.execute(statement).scalars().all()
    canceled_loan_requests = list(canceled_result)

    # 2. 貸出申請の更新
    for loan_request in canceled_loan_requests:
        loan_request.status = LoanStatus.CANCELED.value
        loan_request.canceled_by = None
        loan_request.canceled_at = now
        loan_request.reason = cancel_reason
    db.flush()

    # 3. 戻り値を設定
    return canceled_loan_requests


@dataclass(frozen=True)
class EquipmentCreateRow:
    """備品一括作成の入力1件分（資産番号・備品名・分類・説明・保管場所。備品作成の引数と同じ制限）"""

    asset_number: str
    name: str
    category: str
    description: str
    location: str


def get_equipment_by_id(db: Session, equipment_id: int, for_update: bool = False) -> Equipment | None:
    """
    備品内部ID指定取得

    設計書：設計書/CRUD/備品管理/備品内部ID指定取得

    【処理概要】
    - 内部IDを指定して備品情報を1件取得する（有効・無効を問わない）。
    - 備品の取得・編集・予約状況確認、および貸出申請・承認・貸出の各処理から利用される。

    【パラメータ】
    - db (Session) : DBセッション
    - equipment_id (int) : 備品内部ID（1以上）
    - for_update (bool) : 行ロック要否。Trueの場合はFOR UPDATEで行ロックする（省略時False）

    【戻り値】
    - equipment (Equipment | None) : 備品情報（該当なしはNone）

    【例外処理】
    - なし

    【処理フロー】
    1. equipmentテーブルから内部IDが一致するレコードを取得（有効フラグでは絞り込まない）
    2. 戻り値を設定
    """
    # 1. 備品情報取得
    statement = select(Equipment).where(Equipment.id == equipment_id)
    if for_update:
        # 再取得時に最新値を読むため、populate_existingで既存の読み込み済み値も更新する
        statement = statement.with_for_update().execution_options(populate_existing=True)
    equipment = db.execute(statement).scalar_one_or_none()

    # 2. 戻り値を設定
    return equipment


def get_equipment_by_asset_number(db: Session, asset_number: str) -> Equipment | None:
    """
    備品資産番号指定取得

    設計書：設計書/CRUD/備品管理/備品資産番号指定取得

    【処理概要】
    - 資産番号を指定して備品情報を1件取得する（有効・無効を問わない）。
    - 備品登録時の重複確認から利用される。

    【パラメータ】
    - db (Session) : DBセッション
    - asset_number (str) : 資産番号（1〜32桁）

    【戻り値】
    - equipment (Equipment | None) : 備品情報（該当なしはNone）

    【例外処理】
    - なし

    【処理フロー】
    1. equipmentテーブルから資産番号が完全一致するレコードを取得（無効化済みも対象）
    2. 戻り値を設定
    """
    # 1. 備品情報取得
    statement = select(Equipment).where(Equipment.asset_number == asset_number)
    equipment = db.execute(statement).scalar_one_or_none()

    # 2. 戻り値を設定
    return equipment


def create_equipment(
    db: Session,
    asset_number: str,
    name: str,
    category: str,
    description: str,
    location: str,
    now: datetime,
) -> Equipment:
    """
    備品作成

    設計書：設計書/CRUD/備品管理/備品作成

    【処理概要】
    - 備品を1件登録する。equipmentテーブルへ1件追加する（flushのみ。コミットは呼び出し元）。

    【パラメータ】
    - db (Session) : DBセッション
    - asset_number (str) : 資産番号（1〜32桁）
    - name (str) : 備品名（1〜100桁）
    - category (str) : 分類（1〜50桁）
    - description (str) : 説明（0〜500桁。空文字を許容）
    - location (str) : 保管場所（0〜100桁。空文字を許容）
    - now (datetime) : 現在日時

    【戻り値】
    - equipment (Equipment) : 登録した備品情報（採番された内部IDを含む）

    【例外処理】
    - IntegrityError : 資産番号の一意制約に違反した場合（呼び出し元が409として扱う）

    【処理フロー】
    1. equipmentテーブルへ登録（有効フラグ=TRUE、作成日時・更新日時=現在日時）
    2. 戻り値を設定
    """
    # 1. 備品登録
    equipment = Equipment(
        asset_number=asset_number,
        name=name,
        category=category,
        description=description,
        location=location,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(equipment)
    db.flush()

    # 2. 戻り値を設定
    return equipment


def create_equipments(db: Session, rows: list[EquipmentCreateRow], now: datetime) -> int:
    """
    備品一括作成

    設計書：設計書/CRUD/備品管理/備品一括作成

    【処理概要】
    - 備品を複数件まとめて登録する（CSV一括登録用）。
    - equipmentテーブルへ複数件を追加する（flushのみ。コミットは呼び出し元）。

    【パラメータ】
    - db (Session) : DBセッション
    - rows (list[EquipmentCreateRow]) : 備品登録内容一覧（1〜1,000件。各要素は備品作成の引数と同じ制限）
    - now (datetime) : 現在日時

    【戻り値】
    - count (int) : 登録件数

    【例外処理】
    - IntegrityError : 資産番号の一意制約に違反した場合
      （呼び出し元が409として扱う。1件でも失敗した場合は全件登録されない＝呼び出し元がロールバックする）

    【処理フロー】
    1. 備品登録内容一覧の各要素を、備品作成と同じ内容でequipmentテーブルへ登録
    2. 戻り値を設定
    """
    # 1. 備品一括登録
    equipments = []
    for row in rows:
        equipment = Equipment(
            asset_number=row.asset_number,
            name=row.name,
            category=row.category,
            description=row.description,
            location=row.location,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        equipments.append(equipment)
    db.add_all(equipments)
    db.flush()

    # 2. 戻り値を設定
    count = len(equipments)
    return count


def update_equipment(
    db: Session,
    equipment: Equipment,
    name: str,
    category: str,
    description: str,
    location: str,
    is_active: bool,
    now: datetime,
) -> Equipment:
    """
    備品情報更新

    設計書：設計書/CRUD/備品管理/備品情報更新

    【処理概要】
    - 備品の備品名・分類・説明・保管場所・有効フラグを更新する（資産番号は変更しない）。
    - 取得済みの備品情報を更新する（flushのみ。コミットは呼び出し元）。

    【パラメータ】
    - db (Session) : DBセッション
    - equipment (Equipment) : 更新対象の備品情報（取得済み）
    - name (str) : 備品名（1〜100桁）
    - category (str) : 分類（1〜50桁）
    - description (str) : 説明（0〜500桁）
    - location (str) : 保管場所（0〜100桁）
    - is_active (bool) : 有効フラグ
    - now (datetime) : 現在日時

    【戻り値】
    - equipment (Equipment) : 更新後の備品情報

    【例外処理】
    - なし

    【処理フロー】
    1. 備品名・分類・説明・保管場所・有効フラグ・更新日時を更新
    2. 戻り値を設定
    """
    # 1. 備品情報更新
    equipment.name = name
    equipment.category = category
    equipment.description = description
    equipment.location = location
    equipment.is_active = is_active
    equipment.updated_at = now
    db.flush()

    # 2. 戻り値を設定
    return equipment


def get_equipments(
    db: Session,
    keyword: str | None,
    category: str | None,
    availability: str | None,
    include_inactive: bool,
    page: int,
    page_size: int,
) -> tuple[list[tuple[Equipment, LoanRequest | None, str | None]], int]:
    """
    備品一覧取得

    設計書：設計書/CRUD/備品管理/備品一覧取得

    【処理概要】
    - 条件に合う備品と、各備品の現在の貸出中の申請（借用者氏名を含む）の一覧・総件数を取得する（F03）。
    - キーワード・分類・貸出状況・有効フラグで絞り込み、内部IDの昇順でページ単位に取得する。
    - 貸出中の申請は備品ごとに高々1件（部分一意インデックス）のため、外部結合で行数は増えない。

    【パラメータ】
    - db (Session) : DBセッション
    - keyword (str | None) : 資産番号または備品名の部分一致（省略時は絞り込まない）
    - category (str | None) : 分類の完全一致（省略時は絞り込まない）
    - availability (str | None) : available（貸出可＝貸出中の申請なし）またはlent（貸出中）。省略時は絞り込まない
    - include_inactive (bool) : 無効化済み含む要否。Falseの場合は有効な備品のみ
    - page (int) : ページ番号（1以上）
    - page_size (int) : 1ページの件数（1〜100）

    【戻り値】
    - items (list[tuple[Equipment, LoanRequest | None, str | None]]) :
      （備品情報, 貸出中の申請, 借用者氏名）の一覧。貸出中でない備品の申請・氏名はNone。0件は空リスト
    - total (int) : 総件数
    ※タプル（備品一覧, 総件数）で返却する

    【例外処理】
    - なし

    【処理フロー】
    1. 総件数取得
    2. 一覧取得（内部ID昇順・LIMIT/OFFSET。貸出中の申請と借用者氏名を併せて取得）
    3. 戻り値を設定
    """
    # 貸出中の申請・借用者との外部結合条件（貸出中は備品ごとに高々1件）
    lent_join_condition = and_(LoanRequest.equipment_id == Equipment.id, LoanRequest.status == LoanStatus.LENT.value)
    borrower_join_condition = User.id == LoanRequest.requester_id

    # 絞り込み条件の作成
    conditions = []
    if keyword:
        escaped_keyword = _escape_like(keyword)
        like_pattern = "%" + escaped_keyword + "%"
        asset_number_condition = Equipment.asset_number.ilike(like_pattern, escape="\\")
        name_condition = Equipment.name.ilike(like_pattern, escape="\\")
        conditions.append(or_(asset_number_condition, name_condition))
    if category is not None:
        conditions.append(Equipment.category == category)
    if not include_inactive:
        conditions.append(Equipment.is_active.is_(True))
    if availability == "available":
        conditions.append(LoanRequest.id.is_(None))
    elif availability == "lent":
        conditions.append(LoanRequest.id.is_not(None))

    # 1. 総件数取得
    count_statement = (
        select(func.count(Equipment.id))
        .select_from(Equipment)
        .outerjoin(LoanRequest, lent_join_condition)
        .where(*conditions)
    )
    total = db.execute(count_statement).scalar_one()

    # 2. 一覧取得
    offset = (page - 1) * page_size
    list_statement = (
        select(Equipment, LoanRequest, User.name)
        .select_from(Equipment)
        .outerjoin(LoanRequest, lent_join_condition)
        .outerjoin(User, borrower_join_condition)
        .where(*conditions)
        .order_by(Equipment.id.asc())
        .limit(page_size)
        .offset(offset)
    )
    rows = db.execute(list_statement).all()
    items: list[tuple[Equipment, LoanRequest | None, str | None]] = [
        (equipment, lent_loan, borrower_name) for equipment, lent_loan, borrower_name in rows
    ]

    # 3. 戻り値を設定
    return items, total


def get_categories(db: Session) -> list[str]:
    """
    分類一覧取得

    設計書：設計書/CRUD/備品管理/分類一覧取得

    【処理概要】
    - 検索条件の選択肢として、有効な備品の分類を重複なしで取得する。

    【パラメータ】
    - db (Session) : DBセッション

    【戻り値】
    - categories (list[str]) : 分類の一覧（昇順。0件は空リスト）

    【例外処理】
    - なし

    【処理フロー】
    1. equipmentテーブルから有効な備品の分類を重複を除いて昇順で取得
    2. 戻り値を設定
    """
    # 1. 分類取得
    statement = select(Equipment.category).where(Equipment.is_active.is_(True)).distinct().order_by(Equipment.category)
    result = db.execute(statement).scalars().all()

    # 2. 戻り値を設定
    categories = list(result)
    return categories


def get_existing_asset_numbers(db: Session, asset_numbers: list[str]) -> set[str]:
    """
    既存資産番号一覧取得

    設計書：設計書/CRUD/備品管理/既存資産番号一覧取得

    【処理概要】
    - 指定した資産番号のうち、既にequipmentテーブルに登録されているものを取得する（CSV一括登録の重複確認用）。

    【パラメータ】
    - db (Session) : DBセッション
    - asset_numbers (list[str]) : 資産番号の一覧（最大1,000件。各1〜32桁。0件の場合は検索せず空集合を返す）

    【戻り値】
    - existing (set[str]) : 既に登録されている資産番号（無効化済みも対象。0件は空集合）

    【例外処理】
    - なし

    【処理フロー】
    1. 資産番号の一覧が0件の場合は、検索せずに空集合を返す
    2. equipmentテーブルから、資産番号が一覧に含まれるものを取得
    3. 戻り値を設定
    """
    # 1. 0件の場合は検索しない
    if not asset_numbers:
        return set()

    # 2. 既存資産番号取得
    statement = select(Equipment.asset_number).where(Equipment.asset_number.in_(asset_numbers))
    result = db.execute(statement).scalars().all()

    # 3. 戻り値を設定
    existing = set(result)
    return existing


def count_open_loans_by_equipment(db: Session, equipment_id: int) -> int:
    """
    備品未完了申請件数取得

    設計書：設計書/CRUD/備品管理/備品未完了申請件数取得

    【処理概要】
    - 備品に紐づく、申請中・承認済み・貸出中の申請の件数を取得する（備品の無効化可否の判定用）。

    【パラメータ】
    - db (Session) : DBセッション
    - equipment_id (int) : 備品内部ID（1以上）

    【戻り値】
    - count (int) : 件数

    【例外処理】
    - なし

    【処理フロー】
    1. loan_requestテーブルから、備品が一致し状態が申請中・承認済み・貸出中の件数を取得
    2. 戻り値を設定
    """
    # 1. 件数取得
    open_statuses = [LoanStatus.REQUESTED.value, LoanStatus.APPROVED.value, LoanStatus.LENT.value]
    statement = (
        select(func.count())
        .select_from(LoanRequest)
        .where(LoanRequest.equipment_id == equipment_id, LoanRequest.status.in_(open_statuses))
    )
    count = db.execute(statement).scalar_one()

    # 2. 戻り値を設定
    return count


def get_lent_loan_by_equipment(db: Session, equipment_id: int) -> tuple[LoanRequest, str] | None:
    """
    貸出中申請取得

    設計書：設計書/CRUD/備品管理/貸出中申請取得

    【処理概要】
    - 備品を指定して、現在貸出中の申請（借用者氏名を含む）を取得する（備品ごとに高々1件）。
    - 備品取得（F03）で現在の貸出状況を返す際に利用される。

    【パラメータ】
    - db (Session) : DBセッション
    - equipment_id (int) : 備品内部ID（1以上）

    【戻り値】
    - lent_loan (tuple[LoanRequest, str] | None) : （貸出中の申請, 借用者氏名）。貸出中でない場合はNone

    【例外処理】
    - なし

    【処理フロー】
    1. loan_requestテーブルから、備品が一致し状態が貸出中の申請を借用者氏名とともに取得
    2. 戻り値を設定
    """
    # 1. 貸出中申請取得
    statement = (
        select(LoanRequest, User.name)
        .join(User, User.id == LoanRequest.requester_id)
        .where(LoanRequest.equipment_id == equipment_id, LoanRequest.status == LoanStatus.LENT.value)
    )
    row = db.execute(statement).first()

    # 2. 戻り値を設定
    if row is None:
        return None
    lent_loan = (row[0], row[1])
    return lent_loan


def get_reservations(db: Session, equipment_id: int, base_date: date) -> list[tuple[LoanRequest, str]]:
    """
    予約期間一覧取得

    設計書：設計書/CRUD/備品管理/予約期間一覧取得

    【処理概要】
    - 備品の承認済み・貸出中の申請（予約期間）を、借用者氏名とともに取得する（F04）。
    - 備品が一致し状態が承認済みまたは貸出中で、占有終了日が基準日以降の申請を開始日の昇順で取得する。

    【パラメータ】
    - db (Session) : DBセッション
    - equipment_id (int) : 備品内部ID（1以上）
    - base_date (date) : 基準日（JST今日。これより前に占有が終わっている申請は返却しない）

    【戻り値】
    - reservations (list[tuple[LoanRequest, str]]) : （貸出申請, 借用者氏名）の一覧。0件は空リスト

    【例外処理】
    - なし

    【処理フロー】
    1. loan_requestテーブルから、備品が一致し状態が承認済み・貸出中の申請を開始日の昇順で取得
       （貸出中は返却されるまで占有するため常に含める。承認済みは返却予定日が基準日以降のもののみ）
    2. 戻り値を設定
    """
    # 1. 予約期間取得
    statuses = [LoanStatus.APPROVED.value, LoanStatus.LENT.value]
    is_lent = LoanRequest.status == LoanStatus.LENT.value
    is_not_ended = LoanRequest.due_date >= base_date
    statement = (
        select(LoanRequest, User.name)
        .join(User, User.id == LoanRequest.requester_id)
        .where(
            LoanRequest.equipment_id == equipment_id,
            LoanRequest.status.in_(statuses),
            or_(is_lent, is_not_ended),
        )
        .order_by(LoanRequest.start_date.asc(), LoanRequest.id.asc())
    )
    rows = db.execute(statement).all()

    # 2. 戻り値を設定
    reservations = [(loan, borrower_name) for loan, borrower_name in rows]
    return reservations


# ---- 貸出申請・承認 ----


def count_overlapping_reservations(
    db: Session,
    equipment_id: int,
    start_date: date,
    due_date: date,
    base_date: date,
    exclude_loan_request_id: int | None = None,
) -> int:
    """
    占有期間重複件数取得

    設計書：設計書/CRUD/貸出申請・承認/占有期間重複件数取得

    【処理概要】
    - 指定した備品・期間に重複する、承認済み・貸出中の申請の件数を取得する（二重貸出の防止）。
    - 貸出申請・申請承認から利用される。呼び出し元が備品を行ロックしたうえで呼び出すこと。

    【パラメータ】
    - db (Session) : DBセッション
    - equipment_id (int) : 備品内部ID（1以上）
    - start_date (date) : 判定対象の期間の開始日（両端を含む）
    - due_date (date) : 判定対象の期間の終了日（両端を含む）
    - base_date (date) : 基準日（JST今日。貸出中の占有終了日の算出に使う）
    - exclude_loan_request_id (int | None) : 除外する貸出申請内部ID（承認時に自分自身を除外する。省略時は除外しない）

    【戻り値】
    - overlap_count (int) : 重複件数（0以上）

    【例外処理】
    - なし

    【処理フロー】
    1. loan_requestテーブルから、備品が一致し状態が承認済み・貸出中で占有期間が重なる申請を数える
       - 占有終了日は、承認済みは返却予定日、貸出中は返却予定日と基準日のうち遅い方（期限超過中は今日まで占有）
    2. 戻り値を設定
    """
    # 1. 重複件数取得
    occupied_statuses = [LoanStatus.APPROVED.value, LoanStatus.LENT.value]
    lent_end_date = func.greatest(LoanRequest.due_date, base_date)
    occupied_end_date = case((LoanRequest.status == LoanStatus.LENT.value, lent_end_date), else_=LoanRequest.due_date)
    conditions = [
        LoanRequest.equipment_id == equipment_id,
        LoanRequest.status.in_(occupied_statuses),
        LoanRequest.start_date <= due_date,
        occupied_end_date >= start_date,
    ]
    if exclude_loan_request_id is not None:
        conditions.append(LoanRequest.id != exclude_loan_request_id)
    statement = select(func.count(LoanRequest.id)).where(*conditions)
    overlap_count = db.execute(statement).scalar_one()

    # 2. 戻り値を設定
    return overlap_count


def get_loan_requests(
    db: Session,
    requester_id: int | None,
    status: str | None,
    newest_first: bool,
    page: int,
    page_size: int,
) -> tuple[list[tuple[LoanRequest, Equipment, User]], int]:
    """
    貸出申請一覧取得

    設計書：設計書/CRUD/貸出申請・承認/貸出申請一覧取得

    【処理概要】
    - 条件に合う貸出申請（備品・申請者の情報を含む）の一覧・総件数を取得する。
    - 自分の申請一覧取得（申請者を指定）と承認待ち申請一覧取得（申請者を指定しない）から利用される。

    【パラメータ】
    - db (Session) : DBセッション
    - requester_id (int | None) : 申請者内部ID（省略時は申請者で絞り込まない）
    - status (str | None) : 状態（申請状態の列挙値。省略時は絞り込まない）
    - newest_first (bool) : 新しい順要否（Trueは申請日時の降順、Falseは昇順）
    - page (int) : ページ番号（1以上）
    - page_size (int) : 1ページの件数（1〜100）

    【戻り値】
    - rows_and_total (tuple[list[tuple[LoanRequest, Equipment, User]], int]) : （明細一覧, 総件数）。該当なしは空リスト

    【例外処理】
    - なし

    【処理フロー】
    1. 条件に合う貸出申請の総件数を取得
    2. 貸出申請・備品・申請者を結合し、申請日時と内部IDの順でページ単位に取得
    3. 戻り値を設定
    """
    # 1. 総件数取得
    conditions = []
    if requester_id is not None:
        conditions.append(LoanRequest.requester_id == requester_id)
    if status is not None:
        conditions.append(LoanRequest.status == status)
    count_statement = select(func.count(LoanRequest.id)).where(*conditions)
    total = db.execute(count_statement).scalar_one()

    # 2. 一覧取得（同時刻でも順序が安定するよう内部IDを併用する）
    if newest_first:
        order_columns = (LoanRequest.requested_at.desc(), LoanRequest.id.desc())
    else:
        order_columns = (LoanRequest.requested_at.asc(), LoanRequest.id.asc())
    offset = (page - 1) * page_size
    list_statement = (
        select(LoanRequest, Equipment, User)
        .join(Equipment, Equipment.id == LoanRequest.equipment_id)
        .join(User, User.id == LoanRequest.requester_id)
        .where(*conditions)
        .order_by(*order_columns)
        .limit(page_size)
        .offset(offset)
    )
    result_rows = db.execute(list_statement).all()
    rows: list[tuple[LoanRequest, Equipment, User]] = [(row[0], row[1], row[2]) for row in result_rows]

    # 3. 戻り値を設定
    rows_and_total = (rows, total)
    return rows_and_total


def create_loan_request(
    db: Session,
    equipment_id: int,
    requester_id: int,
    start_date: date,
    due_date: date,
    purpose: str,
    now: datetime,
) -> LoanRequest:
    """
    貸出申請作成

    設計書：設計書/CRUD/貸出申請・承認/貸出申請作成

    【処理概要】
    - 貸出申請を「申請中」で新規登録する（flushして内部IDを確定する。コミットは呼び出し元）。

    【パラメータ】
    - db (Session) : DBセッション
    - equipment_id (int) : 備品内部ID（1以上）
    - requester_id (int) : 申請者内部ID（認証済みユーザーの内部ID）
    - start_date (date) : 開始日（JSTの暦日）
    - due_date (date) : 返却予定日（開始日以降。呼び出し元で検証済み）
    - purpose (str) : 用途（1〜200桁）
    - now (datetime) : 現在日時（UTC。申請日時に設定する）

    【戻り値】
    - loan_request (LoanRequest) : 作成した貸出申請

    【例外処理】
    - なし

    【処理フロー】
    1. loan_requestテーブルへ、状態を申請中・理由とメモを空文字として1件追加し、flushする
    2. 戻り値を設定
    """
    # 1. 貸出申請レコードの作成
    loan_request = LoanRequest(
        equipment_id=equipment_id,
        requester_id=requester_id,
        start_date=start_date,
        due_date=due_date,
        purpose=purpose,
        status=LoanStatus.REQUESTED.value,
        reason="",
        return_note="",
        requested_at=now,
    )
    db.add(loan_request)
    db.flush()

    # 2. 戻り値を設定
    return loan_request


def get_loan_request_by_id(db: Session, loan_request_id: int, for_update: bool = False) -> LoanRequest | None:
    """
    貸出申請内部ID指定取得

    設計書：設計書/CRUD/貸出申請・承認/貸出申請内部ID指定取得

    【処理概要】
    - 内部IDを指定して貸出申請を1件取得する（状態・申請者では絞り込まない）。
    - 申請取消・承認・却下・管理者取消の各処理から利用される。

    【パラメータ】
    - db (Session) : DBセッション
    - loan_request_id (int) : 貸出申請内部ID（1以上）
    - for_update (bool) : 行ロック要否（Trueの場合はFOR UPDATEで行ロックし最新の状態を返す。省略時False）

    【戻り値】
    - loan_request (LoanRequest | None) : 貸出申請情報（該当なしはNone）

    【例外処理】
    - なし

    【処理フロー】
    1. loan_requestテーブルから内部IDが一致するレコードを取得
    2. 戻り値を設定
    """
    # 1. 貸出申請取得
    statement = select(LoanRequest).where(LoanRequest.id == loan_request_id)
    if for_update:
        # 再取得時に最新値を読むため、populate_existingで既存の読み込み済み値も更新する
        statement = statement.with_for_update().execution_options(populate_existing=True)
    loan_request = db.execute(statement).scalar_one_or_none()

    # 2. 戻り値を設定
    return loan_request


def cancel_loan_request(
    db: Session,
    loan_request: LoanRequest,
    canceled_by: int,
    reason: str,
    now: datetime,
) -> LoanRequest:
    """
    貸出申請取消

    設計書：設計書/CRUD/貸出申請・承認/貸出申請取消

    【処理概要】
    - 貸出申請を取消へ更新する（flushのみ。コミットは呼び出し元）。

    【パラメータ】
    - db (Session) : DBセッション
    - loan_request (LoanRequest) : 貸出申請情報（行ロック取得済み・取消可能な状態であることを呼び出し元で検証済み）
    - canceled_by (int) : 取消した者内部ID（申請者本人または管理者）
    - reason (str) : 理由（管理者取消時は1文字以上。申請者本人の取消は空文字）
    - now (datetime) : 現在日時（UTC）

    【戻り値】
    - loan_request (LoanRequest) : 更新した貸出申請

    【例外処理】
    - なし

    【処理フロー】
    1. 状態を取消、取消した者・取消日時・理由を設定して、flushする
    2. 戻り値を設定
    """
    # 1. 貸出申請の更新
    loan_request.status = LoanStatus.CANCELED.value
    loan_request.canceled_by = canceled_by
    loan_request.canceled_at = now
    loan_request.reason = reason
    db.flush()

    # 2. 戻り値を設定
    return loan_request


def decide_loan_request(
    db: Session,
    loan_request: LoanRequest,
    new_status: str,
    decided_by: int,
    reason: str,
    now: datetime,
) -> LoanRequest:
    """
    貸出申請承認却下

    設計書：設計書/CRUD/貸出申請・承認/貸出申請承認却下

    【処理概要】
    - 申請中の貸出申請を、承認済みまたは却下へ更新する（flushのみ。コミットは呼び出し元）。

    【パラメータ】
    - db (Session) : DBセッション
    - loan_request (LoanRequest) : 貸出申請情報（行ロック取得済み・申請中であることを呼び出し元で検証済み）
    - new_status (str) : 新しい状態（approvedまたはrejected）
    - decided_by (int) : 処理した管理者内部ID
    - reason (str) : 理由（却下時は1文字以上。承認時は空文字）
    - now (datetime) : 現在日時（UTC）

    【戻り値】
    - loan_request (LoanRequest) : 更新した貸出申請

    【例外処理】
    - IntegrityError : 承認済みへの更新時、排他制約（ex_loan_request_equipment_period）に違反した場合
      （呼び出し元が409へ変換する）

    【処理フロー】
    1. 状態・処理した管理者・承認却下日時・理由を設定して、flushする
    2. 戻り値を設定
    """
    # 1. 貸出申請の更新
    loan_request.status = new_status
    loan_request.decided_by = decided_by
    loan_request.decided_at = now
    loan_request.reason = reason
    db.flush()

    # 2. 戻り値を設定
    return loan_request


def get_loan_request_detail(db: Session, loan_request_id: int) -> tuple[LoanRequest, Equipment, User] | None:
    """
    貸出申請詳細取得

    設計書：設計書/CRUD/貸出申請・承認/貸出申請詳細取得

    【処理概要】
    - 内部IDを指定して、貸出申請と、その備品・申請者の情報を1件取得する（レスポンス生成用）。
    - 備品・ユーザーは有効・無効を問わない（履歴として無効化済みの氏名・備品名を表示するため）。

    【パラメータ】
    - db (Session) : DBセッション
    - loan_request_id (int) : 貸出申請内部ID（1以上）

    【戻り値】
    - detail (tuple[LoanRequest, Equipment, User] | None) : （貸出申請, 備品, 申請者）。該当なしはNone

    【例外処理】
    - なし

    【処理フロー】
    1. loan_request・equipment・app_userを結合して取得
    2. 戻り値を設定
    """
    # 1. 貸出申請詳細取得
    statement = (
        select(LoanRequest, Equipment, User)
        .join(Equipment, Equipment.id == LoanRequest.equipment_id)
        .join(User, User.id == LoanRequest.requester_id)
        .where(LoanRequest.id == loan_request_id)
    )
    row = db.execute(statement).first()

    # 2. 戻り値を設定
    if row is None:
        return None
    detail = (row[0], row[1], row[2])
    return detail
