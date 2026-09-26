"""
日次処理（スケジューラー）

【概要】
毎日6:00（JST）に、貸出・返却の運用に必要な日次の自動処理を起動する。
承認済みで返却予定日を過ぎた申請・申請中で開始日を過ぎた申請の自動取消と、返却期限・期限超過の通知を、
サービス層の日次処理として呼び出す。業務判断はサービス層が行い、本ファイルは起動・結果の記録だけを担当する。
アプリケーションの起動時に`start_scheduler`、終了時に`stop_scheduler`を呼ぶ（起動時に日次処理そのものは実行しない）。

設計書：設計書/日次処理（scheduler）/
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import services
from app.database import get_session_factory
from app.schemas import JST

logger = logging.getLogger("app")

# 日次処理を登録したスケジューラー（起動中のみ保持する。状態を持つ必要があるためモジュール変数とする）
_scheduler: BackgroundScheduler | None = None

_DAILY_JOB_ID = "daily_job"
# 実行遅延の許容（秒）。6:00に実行できなかった場合、6時間以内に実行可能になれば実行する
_MISFIRE_GRACE_SECONDS = 21600


def run_daily_job() -> None:
    """
    日次処理実行

    設計書：設計書/日次処理（scheduler）/日次処理実行

    【処理概要】
    - スケジューラーから引数なしで起動され、サービス層の日次処理を呼び出して結果をログに記録する。
    - 想定外の例外もここで捕捉し、スケジューラーには伝えない（次回のスケジュールは継続する）。

    【パラメータ】
    - なし

    【戻り値】
    - なし

    【例外処理】
    - 想定外の例外が発生：捕捉してERRORで記録し、スケジューラーには例外を伝えない

    【処理フロー】
    1. データベースセッションを生成
    2. 日次処理（サービス層）を呼び出す
    3. 結果をログに記録（申請・ユーザーの個別情報は記録しない）
    4. データベースセッションを閉じる（必ず実行）
    """
    # 1. データベースセッション生成
    session_factory = get_session_factory()
    db = session_factory()
    try:
        # 2. 日次処理の呼び出し
        result = services.run_daily_job(db)

        # 3. 結果の記録
        failed_steps = result["failed_steps"]
        if failed_steps:
            logger.error("日次処理で失敗した段階があります: 失敗した段階=%s 結果=%s", failed_steps, result)
        else:
            logger.info("日次処理が完了しました: 結果=%s", result)
    except Exception as error:
        error_type = type(error).__name__
        logger.error("日次処理で想定外の例外が発生しました: 例外=%s", error_type)
    finally:
        # 4. セッションを閉じる
        db.close()


def start_scheduler() -> None:
    """
    日次処理登録（スケジューラーの登録・開始）

    設計書：設計書/日次処理（scheduler）/日次処理登録

    【処理概要】
    - 日次処理を毎日6:00（JST）に実行するよう登録し、スケジューラーを開始する。
    - アプリケーション起動時に日次処理そのものは実行しない（登録のみ）。

    【パラメータ】
    - なし

    【戻り値】
    - なし

    【例外処理】
    - なし

    【処理フロー】
    1. スケジューラーをタイムゾーンAsia/Tokyoで生成
    2. 日次処理を登録（毎日6時0分・ジョブID固定・同時実行1・実行遅延6時間まで許容・遅延分は1回にまとめる）
    3. スケジューラーを開始
    """
    global _scheduler

    # 1. スケジューラー生成
    scheduler = BackgroundScheduler(timezone=JST)

    # 2. 日次処理の登録
    trigger = CronTrigger(hour=6, minute=0, timezone=JST)
    scheduler.add_job(
        run_daily_job,
        trigger,
        id=_DAILY_JOB_ID,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=_MISFIRE_GRACE_SECONDS,
        coalesce=True,
    )

    # 3. スケジューラー開始
    scheduler.start()
    _scheduler = scheduler
    logger.info("日次処理のスケジューラーを開始しました")


def stop_scheduler() -> None:
    """
    日次処理停止（スケジューラーの停止）

    設計書：設計書/日次処理（scheduler）/日次処理登録

    【処理概要】
    - アプリケーション終了時に、実行中のジョブの完了を待たずにスケジューラーを停止する。
      途中で中断されても、各段階はトランザクション単位でコミットされ、次回の実行で未処理分が処理される。

    【パラメータ】
    - なし

    【戻り値】
    - なし

    【例外処理】
    - なし（開始されていない場合は何もしない）

    【処理フロー】
    1. 開始済みのスケジューラーを停止（wait=False）
    """
    global _scheduler

    # 1. スケジューラー停止
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("日次処理のスケジューラーを停止しました")
