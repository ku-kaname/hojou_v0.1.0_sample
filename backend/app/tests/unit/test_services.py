"""
単体テスト：サービス層（現在日時取得・今日取得）

設計書：設計書/サーバー処理（main）/共通/日時取得
"""

from datetime import UTC, date, datetime

from app import services


def test_get_now_is_utc_aware():
    now = services.get_now()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 0


def test_get_today_converts_to_jst(monkeypatch):
    # UTC 2026-09-25 15:00 は JST 2026-09-26 00:00
    monkeypatch.setattr(services, "get_now", lambda: datetime(2026, 9, 25, 15, 0, tzinfo=UTC))
    assert services.get_today() == date(2026, 9, 26)


def test_get_today_before_jst_midnight(monkeypatch):
    # UTC 2026-09-25 14:59 は JST 2026-09-25 23:59
    monkeypatch.setattr(services, "get_now", lambda: datetime(2026, 9, 25, 14, 59, tzinfo=UTC))
    assert services.get_today() == date(2026, 9, 25)
