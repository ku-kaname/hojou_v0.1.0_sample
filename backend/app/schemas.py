"""
スキーマ（共通）

【概要】
全機能から共通で使うリクエスト・レスポンスの型定義。
エラーレスポンス・ページング条件・ページング結果・自分のユーザー情報レスポンス・認証済みユーザー・
ヘルスチェックレスポンスを定義する。日時はJST（+09:00）のISO 8601形式で返却するための型も提供する。

設計書：設計書/スキーマ（schemas）/共通
"""

from datetime import UTC, datetime
from typing import Annotated, Generic, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

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
