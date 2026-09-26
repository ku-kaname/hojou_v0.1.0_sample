"""
SQLAlchemyテーブルモデル

【概要】
app_user（ユーザー）・equipment（備品）・loan_request（貸出申請）・notification（通知）のテーブル定義。
列挙値（ロール・申請状態・通知種別）の一次情報は設計書「テーブル定義（models）」であり、
本ファイルの列挙型はその内容と一致させる。

設計書：設計書/テーブル定義（models）
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Role(enum.StrEnum):
    """ロール（app_user.role）"""

    GENERAL = "general"
    ADMIN = "admin"


class LoanStatus(enum.StrEnum):
    """申請状態（loan_request.status）"""

    REQUESTED = "requested"
    APPROVED = "approved"
    LENT = "lent"
    RETURNED = "returned"
    REJECTED = "rejected"
    CANCELED = "canceled"


class NotificationType(enum.StrEnum):
    """通知種別（notification.type）"""

    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELED = "canceled"
    NEW_REQUEST = "new_request"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"


def _in_clause(column: str, values: type[enum.StrEnum]) -> str:
    """CHECK制約用に「列 IN ('値1', '値2', ...)」の文字列を組み立てる（値は本ファイルの列挙型のみ）"""
    quoted = ", ".join(f"'{member.value}'" for member in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    """全テーブルモデルの基底クラス"""


class User(Base):
    """ユーザー（app_user）"""

    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint(_in_clause("role", Role), name="ck_app_user_role"),
        CheckConstraint("token_generation >= 0", name="ck_app_user_token_generation"),
        CheckConstraint("failed_login_count >= 0", name="ck_app_user_failed_login_count"),
        CheckConstraint("login_id ~ '^[A-Za-z0-9_-]+$'", name="ck_app_user_login_id_format"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    login_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    department: Mapped[str] = mapped_column(String(50), default="", server_default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=Role.GENERAL.value, server_default=Role.GENERAL.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    token_generation: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Equipment(Base):
    """備品（equipment）"""

    __tablename__ = "equipment"
    __table_args__ = (CheckConstraint("asset_number ~ '^[A-Za-z0-9-]+$'", name="ck_equipment_asset_number_format"),)

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    asset_number: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50), index=True)
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    location: Mapped[str] = mapped_column(String(100), default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LoanRequest(Base):
    """貸出申請（loan_request）。貸出履歴を兼ねる"""

    __tablename__ = "loan_request"
    __table_args__ = (
        CheckConstraint("due_date >= start_date", name="ck_loan_request_period"),
        CheckConstraint(_in_clause("status", LoanStatus), name="ck_loan_request_status"),
        Index("ix_loan_request_equipment_status", "equipment_id", "status"),
        Index("ix_loan_request_requester", "requester_id", "requested_at"),
        Index("ix_loan_request_status_due", "status", "due_date"),
        # 同一備品の貸出中は1件のみ
        Index(
            "uq_loan_request_equipment_lent",
            "equipment_id",
            unique=True,
            postgresql_where=text("status = 'lent'"),
        ),
        # 承認済み・貸出中の予定期間の重複を防止（二重貸出の最終防衛線）。btree_gist拡張が必要
        ExcludeConstraint(
            ("equipment_id", "="),
            (text("daterange(start_date, due_date, '[]')"), "&&"),
            where=text("status IN ('approved', 'lent')"),
            using="gist",
            name="ex_loan_request_equipment_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id", ondelete="RESTRICT"), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    purpose: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(16), default=LoanStatus.REQUESTED.value, server_default=LoanStatus.REQUESTED.value, index=True
    )
    reason: Mapped[str] = mapped_column(String(200), default="", server_default="")
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True)
    lent_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True)
    returned_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True)
    canceled_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_note: Mapped[str] = mapped_column(String(200), default="", server_default="")


class Notification(Base):
    """通知（notification）"""

    __tablename__ = "notification"
    __table_args__ = (
        CheckConstraint(_in_clause("type", NotificationType), name="ck_notification_type"),
        Index("ix_notification_recipient_read_created", "recipient_id", "is_read", "created_at"),
        # 日次通知（返却期限）の同日重複生成を防ぐ（日次処理の冪等化）
        Index(
            "uq_notification_daily",
            "recipient_id",
            "type",
            "loan_request_id",
            "notified_date",
            unique=True,
            postgresql_where=text("type IN ('due_soon', 'overdue')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"))
    type: Mapped[str] = mapped_column(String(16))
    loan_request_id: Mapped[int] = mapped_column(ForeignKey("loan_request.id", ondelete="RESTRICT"), index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notified_date: Mapped[date] = mapped_column(Date)
