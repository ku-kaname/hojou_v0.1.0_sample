"""
データアクセス層（CRUD）

【概要】
DBへの読み書きだけを担当する。業務判断・commitは行わない（書き込み後は`flush`まで。commitはサービス層が行う）。
本ファイルは機能群ごとの実装に伴い関数が追加される。

設計書：設計書/CRUD/
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Notification, NotificationType, User

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
