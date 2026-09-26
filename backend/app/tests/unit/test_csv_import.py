"""
単体テスト：備品CSV一括登録（CSVの文字コード・解析・1行ごとの検証）

テスト仕様書：単体テスト仕様書/backend/備品管理/備品CSV一括登録
設計書：設計書/サーバー処理（main）/備品管理/備品CSV一括登録
テスト対象ファイル：backend/app/services.py
  （_decode_csv_bytes・_parse_csv_rows・_has_control_char・_validate_csv_row・_get_error_row_number）

【テストの考え方】
- データベースは使わない。文字列・バイト列を関数へ渡し、戻り値または例外を確認する。
- 「CSV全体を取り込む流れ」（データベースへの登録まで）は結合テストで確認する。
- コメントの「項番」は、単体テスト仕様書の項番を表す。
"""

import pytest
from fastapi import HTTPException

from app import services
from app.schemas import CsvRowError

# 誤りのない1行分のセル（資産番号・備品名・分類・説明・保管場所）
VALID_CELLS = ["A-001", "ノートPC", "PC", "会議用", "1F 倉庫"]


def _cells(**changes: str) -> list[str]:
    """誤りのない行を基に、指定した列だけ値を差し替えた行を作る"""
    index_by_name = {"asset": 0, "name": 1, "category": 2, "description": 3, "location": 4}
    cells = list(VALID_CELLS)
    for key, value in changes.items():
        cells[index_by_name[key]] = value
    return cells


def _messages(errors: list[CsvRowError]) -> list[tuple[int, str | None, str]]:
    """行別エラーを（行番号, 列, メッセージ）の一覧にして比較しやすくする"""
    return [(error.row_number, error.column, error.message) for error in errors]


# ---------------- _decode_csv_bytes（項番1〜3） ----------------


def test_decode_csv_bytes_removes_bom():
    """項番1：BOM付きUTF-8は、BOMを除いた文字列になる"""
    content = "資産番号,備品名".encode("utf-8-sig")
    assert services._decode_csv_bytes(content) == "資産番号,備品名"


def test_decode_csv_bytes_without_bom():
    """項番2：BOMなしのUTF-8（日本語）はそのまま文字列になる"""
    content = "資産番号,備品名".encode("utf-8")
    assert services._decode_csv_bytes(content) == "資産番号,備品名"


def test_decode_csv_bytes_rejects_shift_jis():
    """項番3：Shift_JISのCSVは、文字コードエラー（400）になる"""
    content = "資産番号,備品名".encode("shift_jis")
    with pytest.raises(HTTPException) as error:
        services._decode_csv_bytes(content)
    assert error.value.status_code == 400
    assert error.value.detail == "CSVの文字コードがUTF-8ではありません"


# ---------------- _parse_csv_rows（項番4〜10） ----------------


def test_parse_csv_rows_line_numbers():
    """項番4：ヘッダー1行目・データ2行目・3行目と、ファイル上の行番号が付く"""
    rows = services._parse_csv_rows("a,b\n1,2\n3,4\n")
    assert rows == [(1, ["a", "b"]), (2, ["1", "2"]), (3, ["3", "4"])]


def test_parse_csv_rows_crlf():
    """項番5：改行がCRLF（Windows形式）でも、行番号はLFの場合と同じ"""
    rows_lf = services._parse_csv_rows("a,b\n1,2\n3,4\n")
    rows_crlf = services._parse_csv_rows("a,b\r\n1,2\r\n3,4\r\n")
    assert rows_crlf == rows_lf


def test_parse_csv_rows_skips_blank_line_but_keeps_line_number():
    """項番6：空行は読み飛ばすが、その後の行の行番号はファイル上の行番号のまま（3行目）"""
    rows = services._parse_csv_rows("a,b\n\n1,2\n")
    assert rows == [(1, ["a", "b"]), (3, ["1", "2"])]


def test_parse_csv_rows_skips_whitespace_only_row():
    """項番7：全セルが空白だけの行は返さない"""
    rows = services._parse_csv_rows("a,b\n , \n1,2\n")
    assert rows == [(1, ["a", "b"]), (3, ["1", "2"])]


def test_parse_csv_rows_multiline_cell():
    """項番8：セル内改行を含む行の行番号は開始行（2）。次の行は改行の分だけ進んで4になる"""
    text = 'a,b\n1,"x\ny"\n3,4\n'
    rows = services._parse_csv_rows(text)
    assert rows == [(1, ["a", "b"]), (2, ["1", "x\ny"]), (4, ["3", "4"])]


def test_parse_csv_rows_quoted_comma():
    """項番9：引用符で囲まれたカンマは区切りではなく、1つのセルの一部になる"""
    rows = services._parse_csv_rows('a,b\n1,"x,y"\n')
    assert rows[1] == (2, ["1", "x,y"])


def test_parse_csv_rows_invalid_quote():
    """項番10：引用符の直後に文字が続く不正な形式は、形式エラー（400）になる"""
    with pytest.raises(HTTPException) as error:
        services._parse_csv_rows('a,b\n1,"x"y\n')
    assert error.value.status_code == 400
    assert error.value.detail == "CSVの形式が正しくありません"


# ---------------- _has_control_char（項番11〜18） ----------------


def test_has_control_char_none():
    """項番11：日本語・英数字だけなら、改行許容の有無にかかわらず制御文字なし"""
    assert services._has_control_char("ノートPC A-001", True) is False
    assert services._has_control_char("ノートPC A-001", False) is False


def test_has_control_char_tab():
    """項番12：タブは、改行を許容する場合でも制御文字として扱う"""
    assert services._has_control_char("a\tb", True) is True


def test_has_control_char_line_break_allowed():
    """項番13：改行（LF・CR）は、許容する指定なら制御文字として扱わない"""
    assert services._has_control_char("a\nb\rc", True) is False


def test_has_control_char_line_break_not_allowed():
    """項番14：改行は、許容しない指定なら制御文字として扱う"""
    assert services._has_control_char("a\nb", False) is True


def test_has_control_char_nul():
    """項番15：NUL文字（0x00）は制御文字として扱う"""
    assert services._has_control_char("a\x00b", True) is True


@pytest.mark.parametrize("code", [0x00, 0x07, 0x0B, 0x1B, 0x1F])
def test_has_control_char_low_range(code):
    """項番16：0x00〜0x1Fの範囲は、両端と中間のどこでも制御文字として扱う"""
    assert services._has_control_char("a" + chr(code) + "b", True) is True


@pytest.mark.parametrize("code", [0x7F, 0x85, 0x9F])
def test_has_control_char_high_range(code):
    """項番17：0x7F〜0x9Fの範囲は、両端と中間のどこでも制御文字として扱う"""
    assert services._has_control_char("a" + chr(code) + "b", True) is True


@pytest.mark.parametrize("code", [0x20, 0xA0])
def test_has_control_char_outside_range(code):
    """項番18：範囲の外側（0x20の空白・0xA0の改行なし空白）は制御文字として扱わない"""
    assert services._has_control_char("a" + chr(code) + "b", True) is False


# ---------------- _validate_csv_row（項番19〜35） ----------------


def test_validate_csv_row_valid():
    """項番19：全項目が正しければ、登録内容が返り、誤りは空になる"""
    row, errors = services._validate_csv_row(2, VALID_CELLS)
    assert errors == []
    assert row is not None
    assert row.asset_number == "A-001"
    assert row.name == "ノートPC"
    assert row.category == "PC"
    assert row.description == "会議用"
    assert row.location == "1F 倉庫"


def test_validate_csv_row_strips_spaces():
    """項番20：各セルの前後の空白は取り除かれて登録内容になる"""
    cells = [" A-001 ", " ノートPC ", " PC ", " 会議用 ", " 1F "]
    row, errors = services._validate_csv_row(2, cells)
    assert errors == []
    assert row is not None
    assert (row.asset_number, row.name, row.category) == ("A-001", "ノートPC", "PC")
    assert (row.description, row.location) == ("会議用", "1F")


def test_validate_csv_row_optional_columns_empty():
    """項番21：説明・保管場所は空でもよい"""
    row, errors = services._validate_csv_row(2, _cells(description="", location=""))
    assert errors == []
    assert row is not None
    assert (row.description, row.location) == ("", "")


def test_validate_csv_row_too_few_columns():
    """項番22：列が4つしかない行は、列数エラー（列名なし）になる"""
    row, errors = services._validate_csv_row(5, VALID_CELLS[:4])
    assert row is None
    assert _messages(errors) == [(5, None, "列数が正しくありません")]


def test_validate_csv_row_too_many_columns():
    """項番23：列が6つある行も、列数エラーになる"""
    row, errors = services._validate_csv_row(2, [*VALID_CELLS, "余分"])
    assert row is None
    assert _messages(errors) == [(2, None, "列数が正しくありません")]


def test_validate_csv_row_asset_number_length_limit():
    """項番24：資産番号32文字は誤りなし"""
    row, errors = services._validate_csv_row(2, _cells(asset="A" * 32))
    assert errors == []
    assert row is not None


def test_validate_csv_row_asset_number_too_long():
    """項番25：資産番号33文字は桁数エラー"""
    row, errors = services._validate_csv_row(2, _cells(asset="A" * 33))
    assert row is None
    assert _messages(errors) == [(2, "資産番号", "資産番号は1〜32文字で入力してください")]


def test_validate_csv_row_asset_number_empty():
    """項番26：資産番号が空なら桁数エラー"""
    row, errors = services._validate_csv_row(2, _cells(asset=""))
    assert row is None
    assert _messages(errors) == [(2, "資産番号", "資産番号は1〜32文字で入力してください")]


@pytest.mark.parametrize("asset", ["A_001", "ＡＢＣ００１"])
def test_validate_csv_row_asset_number_invalid_characters(asset):
    """項番27：資産番号に「_」や全角英数字があれば、使用できる文字のエラー"""
    row, errors = services._validate_csv_row(2, _cells(asset=asset))
    assert row is None
    assert _messages(errors) == [(2, "資産番号", "資産番号は半角英数字と-のみ使用できます")]


def test_validate_csv_row_name_too_long():
    """項番28：備品名101文字は桁数エラー"""
    row, errors = services._validate_csv_row(2, _cells(name="あ" * 101))
    assert row is None
    assert _messages(errors) == [(2, "備品名", "備品名は1〜100文字で入力してください")]


def test_validate_csv_row_name_only_spaces():
    """項番29：備品名が空白だけなら、空白を除くと0文字のため桁数エラー"""
    row, errors = services._validate_csv_row(2, _cells(name="   "))
    assert row is None
    assert _messages(errors) == [(2, "備品名", "備品名は1〜100文字で入力してください")]


def test_validate_csv_row_category_empty():
    """項番30：分類が空なら桁数エラー"""
    row, errors = services._validate_csv_row(2, _cells(category=""))
    assert row is None
    assert _messages(errors) == [(2, "分類", "分類は1〜50文字で入力してください")]


def test_validate_csv_row_description_length_limit():
    """項番31：説明は500文字まで誤りなし、501文字は桁数エラー"""
    row, errors = services._validate_csv_row(2, _cells(description="あ" * 500))
    assert errors == []
    assert row is not None

    row, errors = services._validate_csv_row(2, _cells(description="あ" * 501))
    assert row is None
    assert _messages(errors) == [(2, "説明", "説明は0〜500文字で入力してください")]


def test_validate_csv_row_location_too_long():
    """項番32：保管場所101文字は桁数エラー"""
    row, errors = services._validate_csv_row(2, _cells(location="あ" * 101))
    assert row is None
    assert _messages(errors) == [(2, "保管場所", "保管場所は0〜100文字で入力してください")]


def test_validate_csv_row_description_allows_line_break():
    """項番33：説明だけは改行を含めてよく、改行は保持される"""
    row, errors = services._validate_csv_row(2, _cells(description="1行目\n2行目"))
    assert errors == []
    assert row is not None
    assert row.description == "1行目\n2行目"


def test_validate_csv_row_name_rejects_line_break_and_tab():
    """項番34：備品名に改行やタブがあれば、使用できない文字のエラー"""
    for name in ["ノート\nPC", "ノート\tPC"]:
        row, errors = services._validate_csv_row(2, _cells(name=name))
        assert row is None
        assert _messages(errors) == [(2, "備品名", "備品名に使用できない文字が含まれています")]


def test_validate_csv_row_multiple_errors_in_column_order():
    """項番35：1行に誤りが複数あれば、列の順（資産番号→備品名）にすべて返し、登録内容は返さない"""
    row, errors = services._validate_csv_row(2, _cells(asset="", name="あ" * 101))
    assert row is None
    assert _messages(errors) == [
        (2, "資産番号", "資産番号は1〜32文字で入力してください"),
        (2, "備品名", "備品名は1〜100文字で入力してください"),
    ]


# ---------------- _get_error_row_number（項番36） ----------------


def test_get_error_row_number():
    """項番36：行別エラーから行番号を取り出す"""
    error = CsvRowError(row_number=7, column="備品名", message="誤り")
    assert services._get_error_row_number(error) == 7
