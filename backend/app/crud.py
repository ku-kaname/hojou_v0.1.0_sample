"""
データアクセス層（CRUD）

【概要】
DBへの読み書きだけを担当する。業務判断・commitは行わない（書き込み後は`flush`まで。commitはサービス層が行う）。
本ファイルは機能群ごとの実装に伴い関数が追加される。

設計書：設計書/CRUD/
"""

from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import LoanRequest, LoanStatus, Notification, NotificationType, Role, User

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
