"""
サービス層（業務処理）

【概要】
業務ルールの判断とトランザクション（commit）の管理を担当する。
本ファイルは、機能群ごとの実装に伴い関数が追加される。共通部分として、現在日時取得・今日取得・通知生成を持つ。

設計書：設計書/サーバー処理（main）/共通/
"""

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app import crud
from app.schemas import JST


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
