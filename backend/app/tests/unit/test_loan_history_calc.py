"""
単体テスト：貸出履歴の日付範囲・遅延日数・CSV無害化

テスト仕様書：単体テスト仕様書/backend/貸出・返却・履歴/貸出履歴の日付範囲・遅延日数・CSV無害化
設計書：設計書/サーバー処理（main）/貸出・返却・履歴/貸出履歴検索、貸出履歴CSV出力
テスト対象ファイル：backend/app/services.py
  （_to_lent_datetime_range・_calculate_delay_days・_sanitize_csv_cell・_to_loan_history_response）

【テストの考え方】
- データベースは使わない。日付・日時を関数へ渡し、計算結果を確認する。
- 日本時間（JST）はUTCより9時間進んでいる。JSTの日付の区切り（0時）はUTCの前日15:00にあたる。
- コメントの「項番」は、単体テスト仕様書の項番を表す。
"""

from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import services

JST = timezone(timedelta(hours=9))


def _filter(from_date: date | None, to_date: date | None) -> SimpleNamespace:
    """貸出日の範囲（開始・終了）だけを持つ検索条件を作る"""
    return SimpleNamespace(from_date=from_date, to_date=to_date)


def _loan(due_date: date, returned_at: datetime | None = None, lent_at: datetime | None = None) -> SimpleNamespace:
    """遅延日数の計算に必要な項目（返却予定日・返却日時・貸出日時）だけを持つ申請を作る"""
    return SimpleNamespace(due_date=due_date, returned_at=returned_at, lent_at=lent_at)


# ---------------- _to_lent_datetime_range（項番1〜6） ----------------


def test_lent_range_no_condition():
    """項番1：開始・終了とも指定しなければ、範囲なし（どちらもNone）"""
    assert services._to_lent_datetime_range(_filter(None, None)) == (None, None)


def test_lent_range_from_only():
    """項番2：開始だけ指定すると、その日のJST 0時（UTCでは前日15:00）から。終了はなし"""
    lent_from, lent_to = services._to_lent_datetime_range(_filter(date(2026, 9, 26), None))
    assert lent_from == datetime(2026, 9, 25, 15, 0, tzinfo=UTC)
    assert lent_to is None


def test_lent_range_to_only():
    """項番3：終了だけ指定すると、終了日を含めるため翌日のJST 0時（UTCでは当日15:00）まで。開始はなし"""
    lent_from, lent_to = services._to_lent_datetime_range(_filter(None, date(2026, 9, 26)))
    assert lent_from is None
    assert lent_to == datetime(2026, 9, 26, 15, 0, tzinfo=UTC)


def test_lent_range_same_day():
    """項番4：開始・終了が同じ日なら、その1日（JST）を丸ごと含む範囲になる"""
    lent_from, lent_to = services._to_lent_datetime_range(_filter(date(2026, 9, 26), date(2026, 9, 26)))
    assert lent_from == datetime(2026, 9, 25, 15, 0, tzinfo=UTC)
    assert lent_to == datetime(2026, 9, 26, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("to_date", "expected_end"),
    [
        (date(2026, 9, 30), datetime(2026, 9, 30, 15, 0, tzinfo=UTC)),  # 翌日は10/1（JST 0時）
        (date(2026, 12, 31), datetime(2026, 12, 31, 15, 0, tzinfo=UTC)),  # 翌日は翌年1/1（JST 0時）
    ],
)
def test_lent_range_end_at_month_and_year_boundary(to_date, expected_end):
    """項番5：終了日が月末・年末でも、翌月1日・翌年1日のJST 0時までになる"""
    _, lent_to = services._to_lent_datetime_range(_filter(None, to_date))
    assert lent_to == expected_end


def test_lent_range_end_before_start():
    """項番6：終了が開始より前なら、日付範囲エラー（400）"""
    with pytest.raises(HTTPException) as error:
        services._to_lent_datetime_range(_filter(date(2026, 9, 26), date(2026, 9, 25)))
    assert error.value.status_code == 400
    assert error.value.detail == "貸出日終了は貸出日開始以降を指定してください"


# ---------------- _calculate_delay_days（項番7〜13） ----------------

TODAY = date(2026, 9, 26)


def test_delay_days_not_returned():
    """項番7：未返却は「今日 − 返却予定日」（9/26 − 9/20 = 6日）"""
    assert services._calculate_delay_days(_loan(date(2026, 9, 20)), TODAY) == 6


def test_delay_days_due_today():
    """項番8：未返却で返却予定日が今日なら0日"""
    assert services._calculate_delay_days(_loan(TODAY), TODAY) == 0


def test_delay_days_not_negative():
    """項番9：返却予定日が先（今日の3日後）でもマイナスにならず0日"""
    assert services._calculate_delay_days(_loan(date(2026, 9, 29)), TODAY) == 0


def test_delay_days_returned_before_due():
    """項番10：返却予定日より前に返却済みなら0日"""
    loan = _loan(date(2026, 9, 26), returned_at=datetime(2026, 9, 25, 10, 0, tzinfo=JST))
    assert services._calculate_delay_days(loan, TODAY) == 0


def test_delay_days_uses_returned_date_not_today():
    """項番11：返却済みは今日ではなく返却日で数える（9/23 − 9/20 = 3日。今日は9/26）"""
    loan = _loan(date(2026, 9, 20), returned_at=datetime(2026, 9, 23, 10, 0, tzinfo=JST))
    assert services._calculate_delay_days(loan, TODAY) == 3


def test_delay_days_returned_just_after_jst_midnight():
    """項番12：UTCでは9/26 15:30でも、JSTでは9/27 00:30。JSTの日付で数えるので1日遅れ"""
    loan = _loan(date(2026, 9, 26), returned_at=datetime(2026, 9, 26, 15, 30, tzinfo=UTC))
    assert services._calculate_delay_days(loan, TODAY) == 1


def test_delay_days_returned_just_before_jst_midnight():
    """項番13：UTCの9/26 14:59はJSTでは9/26 23:59。まだ9/26なので0日"""
    loan = _loan(date(2026, 9, 26), returned_at=datetime(2026, 9, 26, 14, 59, tzinfo=UTC))
    assert services._calculate_delay_days(loan, TODAY) == 0


# ---------------- _sanitize_csv_cell（項番14〜16） ----------------


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_sanitize_csv_cell_dangerous_prefix(prefix):
    """項番14：Excelで数式として実行されうる先頭文字（= + - @ タブ 復帰）には、先頭に「'」を付ける"""
    value = prefix + "SUM(A1)"
    assert services._sanitize_csv_cell(value) == "'" + value


@pytest.mark.parametrize("value", ["ノートPC", "A-001", "abc", ""])
def test_sanitize_csv_cell_normal_value(value):
    """項番15：通常の文字列（日本語・英数字・空文字）はそのまま返す"""
    assert services._sanitize_csv_cell(value) == value


@pytest.mark.parametrize("value", ["a=b", "PC-01", " =1+1"])
def test_sanitize_csv_cell_dangerous_char_not_at_start(value):
    """項番16：危険な文字が先頭でなければ（途中・空白の後）、そのまま返す"""
    assert services._sanitize_csv_cell(value) == value


# ---------------- _to_loan_history_response（項番17） ----------------


def test_to_loan_history_response_without_lent_at():
    """項番17：貸出日時がない申請は、履歴にできないためエラー（ValueError）"""
    loan = _loan(date(2026, 9, 20), lent_at=None)
    with pytest.raises(ValueError, match="貸出履歴に貸出日時がありません"):
        services._to_loan_history_response(loan, SimpleNamespace(), SimpleNamespace(), TODAY)
