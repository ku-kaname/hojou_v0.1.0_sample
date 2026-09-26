"""
スキーマ（共通）

【概要】
全機能から共通で使うリクエスト・レスポンスの型定義。
エラーレスポンス・ページング条件・ページング結果・自分のユーザー情報レスポンス・認証済みユーザー・
ヘルスチェックレスポンスを定義する。日時はJST（+09:00）のISO 8601形式で返却するための型も提供する。
認証・ユーザー管理の機能群で使うリクエスト・レスポンス（ログイン・パスワード変更・ユーザー登録・編集等）も、
本ファイルの後半にまとめて定義する。
備品管理の機能群で使うリクエスト・レスポンス（備品登録・編集・一覧・予約状況・CSV一括登録等）も同様に定義する。
貸出申請・承認の機能群で使うリクエスト・レスポンス（貸出申請・却下・管理者取消・申請一覧・申請レスポンス）も同様に定義する。
貸出・返却・履歴の機能群で使うリクエスト・レスポンス（返却・貸出履歴条件・貸出履歴レスポンス）も同様に定義する。

設計書：設計書/スキーマ（schemas）/共通、設計書/スキーマ（schemas）/認証・ユーザー管理、設計書/スキーマ（schemas）/備品管理、
設計書/スキーマ（schemas）/貸出申請・承認、
設計書/スキーマ（schemas）/貸出・返却・履歴
"""

from datetime import UTC, date, datetime
from typing import Annotated, Generic, Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints

JST = ZoneInfo("Asia/Tokyo")

T = TypeVar("T")


def _to_jst_iso(value: datetime) -> str:
    """UTC等の日時をJST（+09:00）のISO 8601文字列へ変換する（タイムゾーン無しはUTCとみなす）"""
    aware_value = value
    if aware_value.tzinfo is None:
        aware_value = aware_value.replace(tzinfo=UTC)
    jst_value = aware_value.astimezone(JST)
    return jst_value.isoformat()


# APIで日時を返却する項目の型。他のスキーマで `created_at: JstDatetime` のように使う
JstDatetime = Annotated[datetime, PlainSerializer(_to_jst_iso, return_type=str, when_used="json")]


class ErrorResponse(BaseModel):
    """エラーレスポンス"""

    detail: str = Field(description="日本語のエラーメッセージ")


class PageQuery(BaseModel):
    """ページング条件"""

    page: int = Field(default=1, ge=1, description="ページ番号")
    page_size: int = Field(default=20, ge=1, le=100, description="1ページの件数")


class Page(BaseModel, Generic[T]):
    """ページング結果"""

    items: list[T] = Field(description="明細（空の場合は空配列）")
    total: int = Field(ge=0, description="総件数")
    page: int = Field(ge=1, description="ページ番号")
    page_size: int = Field(ge=1, le=100, description="1ページの件数")


class MeResponse(BaseModel):
    """自分のユーザー情報レスポンス"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(ge=1, description="内部ID")
    login_id: str = Field(min_length=3, max_length=32, description="ユーザーID")
    name: str = Field(min_length=1, max_length=50, description="氏名")
    department: str = Field(max_length=50, description="所属")
    role: str = Field(description="ロール（general / admin）")
    must_change_password: bool = Field(description="真の場合、画面はパスワード変更画面へ誘導する")


class AuthenticatedUser(BaseModel):
    """認証済みユーザー。パスワードハッシュ・トークン世代は含めない（他層へ渡さない）"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(ge=1, description="内部ID")
    login_id: str = Field(min_length=3, max_length=32, description="ユーザーID")
    name: str = Field(min_length=1, max_length=50, description="氏名")
    role: str = Field(description="ロール（general / admin）")
    must_change_password: bool = Field(description="初回パスワード変更要否")


class HealthResponse(BaseModel):
    """ヘルスチェックレスポンス"""

    status: str = Field(default="ok", description="固定値ok")


# ---- 認証・ユーザー管理 ----

# ユーザーIDの形式（半角英数字と_・-）
_LOGIN_ID_PATTERN = r"^[A-Za-z0-9_-]+$"

# ロール（models.Roleの値と一致させる）
RoleValue = Literal["general", "admin"]

# 前後の空白を除去したうえで1〜50桁を検証する氏名の型
NameValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class LoginRequest(BaseModel):
    """ログインリクエスト"""

    login_id: str = Field(min_length=3, max_length=32, pattern=_LOGIN_ID_PATTERN, description="ユーザーID")
    # 最大桁は、Argon2idの過大入力による負荷を避けるため
    password: str = Field(min_length=1, max_length=128, description="パスワード")


class TokenResponse(BaseModel):
    """トークン発行レスポンス"""

    access_token: str = Field(description="発行したJWT")
    token_type: str = Field(default="bearer", description="固定値bearer")
    must_change_password: bool = Field(description="真の場合、画面はパスワード変更画面へ誘導する")


class PasswordChangeRequest(BaseModel):
    """パスワード変更リクエスト。現在と同一の新パスワードは業務ルールで検証する"""

    current_password: str = Field(min_length=1, max_length=128, description="現在のパスワード")
    new_password: str = Field(min_length=8, max_length=128, description="新しいパスワード")


class UserCreateRequest(BaseModel):
    """ユーザー登録リクエスト"""

    login_id: str = Field(min_length=3, max_length=32, pattern=_LOGIN_ID_PATTERN, description="ユーザーID")
    name: NameValue = Field(description="氏名（前後の空白は除去）")
    department: str = Field(default="", max_length=50, description="所属")
    role: RoleValue = Field(description="ロール（general / admin）")
    initial_password: str = Field(min_length=8, max_length=128, description="初期パスワード")


class UserUpdateRequest(BaseModel):
    """ユーザー編集リクエスト（全項目を指定する全置換）"""

    name: NameValue = Field(description="氏名（前後の空白は除去）")
    department: str = Field(max_length=50, description="所属")
    role: RoleValue = Field(description="ロール（general / admin）")
    is_active: bool = Field(description="有効フラグ")


class PasswordResetRequest(BaseModel):
    """パスワード初期化リクエスト"""

    new_password: str = Field(min_length=8, max_length=128, description="初期パスワード")


class UserResponse(BaseModel):
    """ユーザーレスポンス。パスワードハッシュ・連続認証失敗回数・ロック解除日時・トークン世代は含めない"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(ge=1, description="内部ID")
    login_id: str = Field(description="ユーザーID")
    name: str = Field(description="氏名")
    department: str = Field(description="所属")
    role: str = Field(description="ロール（general / admin）")
    is_active: bool = Field(description="有効フラグ")
    must_change_password: bool = Field(description="初回パスワード変更要否")
    created_at: JstDatetime = Field(description="作成日時（JST）")
    updated_at: JstDatetime = Field(description="更新日時（JST）")


class UserListQuery(PageQuery):
    """ユーザー一覧クエリ"""

    keyword: str | None = Field(default=None, max_length=50, description="ユーザーIDまたは氏名の部分一致")
    role: RoleValue | None = Field(default=None, description="ロール（general / admin）")
    is_active: bool | None = Field(default=None, description="省略時は有効・無効の両方")


# ---- 備品管理 ----

# 資産番号の形式（半角英数字と-）
_ASSET_NUMBER_PATTERN = r"^[A-Za-z0-9-]+$"

# 貸出状況（available：貸出可、lent：貸出中）
AvailabilityValue = Literal["available", "lent"]

# 前後の空白を除去したうえで検証する備品名・分類の型
EquipmentNameValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
CategoryValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class EquipmentCreateRequest(BaseModel):
    """備品登録リクエスト"""

    asset_number: str = Field(min_length=1, max_length=32, pattern=_ASSET_NUMBER_PATTERN, description="資産番号")
    name: EquipmentNameValue = Field(description="備品名（前後の空白は除去）")
    category: CategoryValue = Field(description="分類（前後の空白は除去）")
    description: str = Field(default="", max_length=500, description="説明")
    location: str = Field(default="", max_length=100, description="保管場所")


class EquipmentUpdateRequest(BaseModel):
    """備品編集リクエスト（全項目を指定する全置換。資産番号は変更できない）"""

    name: EquipmentNameValue = Field(description="備品名（前後の空白は除去）")
    category: CategoryValue = Field(description="分類（前後の空白は除去）")
    description: str = Field(max_length=500, description="説明")
    location: str = Field(max_length=100, description="保管場所")
    is_active: bool = Field(description="有効フラグ")


class EquipmentListQuery(PageQuery):
    """備品一覧クエリ"""

    keyword: str | None = Field(default=None, max_length=50, description="資産番号または備品名の部分一致")
    category: str | None = Field(default=None, max_length=50, description="分類（完全一致）")
    availability: AvailabilityValue | None = Field(default=None, description="貸出状況（available / lent）")
    include_inactive: bool = Field(default=False, description="無効化済みを含めるか（Trueは管理者のみ）")


class EquipmentResponse(BaseModel):
    """備品レスポンス。貸出状況は貸出中の申請の有無から算出する値（テーブルの列ではない）"""

    id: int = Field(ge=1, description="内部ID")
    asset_number: str = Field(description="資産番号")
    name: str = Field(description="備品名")
    category: str = Field(description="分類")
    description: str = Field(description="説明")
    location: str = Field(description="保管場所")
    is_active: bool = Field(description="有効フラグ")
    availability: AvailabilityValue = Field(description="貸出状況（available / lent）")
    current_due_date: date | None = Field(description="現在の返却予定日（貸出中でない場合はNULL）")
    is_overdue: bool = Field(description="期限超過（貸出中かつ返却予定日が今日より前）")
    current_borrower_name: str | None = Field(description="現在の借用者氏名（管理者にのみ設定）")
    created_at: JstDatetime = Field(description="作成日時（JST）")
    updated_at: JstDatetime = Field(description="更新日時（JST）")


class CategoryListResponse(BaseModel):
    """分類一覧レスポンス"""

    items: list[str] = Field(description="分類（昇順。空の場合は空配列）")


class ReservationResponse(BaseModel):
    """予約期間レスポンス"""

    start_date: date = Field(description="開始日")
    due_date: date = Field(description="返却予定日")
    occupied_until: date = Field(description="占有終了日")
    status: Literal["approved", "lent"] = Field(description="状態（approved：承認済み、lent：貸出中）")
    borrower_name: str | None = Field(description="借用者氏名（管理者にのみ設定）")


class ReservationListResponse(BaseModel):
    """予約状況レスポンス"""

    items: list[ReservationResponse] = Field(description="予約期間一覧（開始日の昇順。空の場合は空配列）")


class CsvImportResponse(BaseModel):
    """CSV一括登録レスポンス"""

    imported_count: int = Field(ge=1, le=1000, description="登録件数")


class CsvRowError(BaseModel):
    """CSV行別エラー"""

    row_number: int = Field(ge=2, description="ヘッダー行を1行目とするファイル上の行番号")
    column: str | None = Field(description="列名（特定の列に依らないエラーはNULL）")
    message: str = Field(description="エラー内容（日本語）")


class CsvImportErrorResponse(ErrorResponse):
    """CSVエラーレスポンス（ステータスコード400）"""

    errors: list[CsvRowError] = Field(description="行別エラー一覧（最大100件。行番号の昇順）")


# ---- 貸出申請・承認 ----

# 申請状態（設計書「テーブル定義（models）」の列挙値）
LoanStatusValue = Literal["requested", "approved", "lent", "returned", "rejected", "canceled"]

PurposeValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ReasonValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class LoanRequestCreateRequest(BaseModel):
    """貸出申請リクエスト。申請者は認証済みユーザーとし、リクエストで指定させない"""

    equipment_id: int = Field(ge=1, description="備品内部ID")
    start_date: date = Field(description="開始日（今日以降であることは業務ルールで検証する）")
    due_date: date = Field(description="返却予定日（開始日以降であることは業務ルールで検証する）")
    purpose: PurposeValue = Field(description="用途（前後の空白は除去）")


class LoanRequestRejectRequest(BaseModel):
    """申請却下リクエスト"""

    reason: ReasonValue = Field(description="理由（前後の空白を除去。空白のみは不可）")


class LoanRequestAdminCancelRequest(BaseModel):
    """申請管理者取消リクエスト"""

    reason: ReasonValue = Field(description="理由（前後の空白を除去。空白のみは不可）")


class LoanRequestListQuery(PageQuery):
    """貸出申請一覧クエリ"""

    status: LoanStatusValue | None = Field(default=None, description="状態（省略時の扱いは各エンドポイントに従う）")


class LoanRequestResponse(BaseModel):
    """申請レスポンス。期限超過・期限超過日数は状態ではなく算出値。操作した管理者の内部IDは含めない"""

    id: int = Field(ge=1, description="内部ID")
    equipment_id: int = Field(ge=1, description="備品内部ID")
    equipment_asset_number: str = Field(description="備品資産番号")
    equipment_name: str = Field(description="備品名")
    requester_id: int = Field(ge=1, description="申請者内部ID")
    requester_name: str = Field(description="申請者氏名")
    requester_department: str = Field(description="申請者所属")
    start_date: date = Field(description="開始日")
    due_date: date = Field(description="返却予定日")
    purpose: str = Field(description="用途")
    status: LoanStatusValue = Field(description="状態")
    reason: str = Field(description="理由（却下・取消。ない場合は空文字）")
    return_note: str = Field(description="返却時状態メモ（返却前は空文字）")
    requested_at: JstDatetime = Field(description="申請日時（JST）")
    decided_at: JstDatetime | None = Field(description="承認却下日時（未処理はnull）")
    lent_at: JstDatetime | None = Field(description="貸出日時（未貸出はnull）")
    returned_at: JstDatetime | None = Field(description="返却日時（未返却はnull）")
    canceled_at: JstDatetime | None = Field(description="取消日時（未取消はnull）")
    is_overdue: bool = Field(description="期限超過（貸出中かつ返却予定日が今日より前）")
    overdue_days: int = Field(ge=0, description="期限超過日数（超過でなければ0）")


# ---- 貸出・返却・履歴 ----

ReturnNoteValue = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class LoanReturnRequest(BaseModel):
    """返却リクエスト。返却日時はクライアントから受け取らず、サーバー時刻を記録する"""

    return_note: ReturnNoteValue = Field(default="", description="返却時状態メモ（前後の空白は除去。省略時は空文字）")


class LoanHistoryFilter(BaseModel):
    """貸出履歴条件。CSV出力はこの条件のみを受け取る（ページングしない）"""

    from_date: date | None = Field(default=None, description="貸出日開始（JST。含む）")
    to_date: date | None = Field(
        default=None, description="貸出日終了（JST。含む。貸出日開始以降であることは業務ルールで検証する）"
    )
    equipment_id: int | None = Field(default=None, ge=1, description="備品内部ID")
    requester_id: int | None = Field(default=None, ge=1, description="借用者内部ID")


class LoanHistoryQuery(LoanHistoryFilter, PageQuery):
    """貸出履歴クエリ。貸出履歴条件にページング条件を加えたもの"""


class LoanHistoryResponse(BaseModel):
    """貸出履歴レスポンス。遅延日数は状態ではなく算出値。操作した管理者の内部IDは含めない"""

    id: int = Field(ge=1, description="貸出申請内部ID")
    equipment_id: int = Field(ge=1, description="備品内部ID")
    equipment_asset_number: str = Field(description="備品資産番号")
    equipment_name: str = Field(description="備品名")
    requester_id: int = Field(ge=1, description="借用者内部ID")
    requester_name: str = Field(description="借用者氏名")
    requester_department: str = Field(description="借用者所属")
    start_date: date = Field(description="開始日")
    due_date: date = Field(description="返却予定日")
    purpose: str = Field(description="用途")
    status: LoanStatusValue = Field(description="状態（貸出中または返却済み）")
    lent_at: JstDatetime = Field(description="貸出日時（JST）")
    returned_at: JstDatetime | None = Field(description="返却日時（貸出中はnull）")
    return_note: str = Field(description="返却時状態メモ（返却前は空文字）")
    delay_days: int = Field(ge=0, description="遅延日数（遅れていなければ0）")
