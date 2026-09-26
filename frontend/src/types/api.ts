/**
 * APIの型定義（リクエスト・レスポンス）
 *
 * 【概要】
 * バックエンド（backend/app/schemas.py）のリクエスト・レスポンスと対応する型を定義する。
 * 日付は`YYYY-MM-DD`、日時はJST（+09:00）のISO 8601形式の文字列で受け渡す。
 *
 * 設計書：設計書/スキーマ（schemas）/、設計書/エンドポイント
 */

/** ロール（general：一般ユーザー、admin：管理者） */
export type Role = 'general' | 'admin'

/** 貸出状況（available：貸出可、lent：貸出中） */
export type Availability = 'available' | 'lent'

/** 申請状態 */
export type LoanStatus = 'requested' | 'approved' | 'lent' | 'returned' | 'rejected' | 'canceled'

/** 通知種別 */
export type NotificationType =
  'approved' | 'rejected' | 'canceled' | 'new_request' | 'due_soon' | 'overdue'

/** ページング結果 */
export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/** ページング条件 */
export interface PageQuery {
  page?: number
  page_size?: number
}

/** エラーレスポンス */
export interface ErrorResponse {
  detail: string
}

// ---- 認証・ユーザー管理 ----

export interface LoginRequest {
  login_id: string
  password: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  must_change_password: boolean
}

export interface PasswordChangeRequest {
  current_password: string
  new_password: string
}

/** 自分のユーザー情報 */
export interface MeResponse {
  id: number
  login_id: string
  name: string
  department: string
  role: Role
  must_change_password: boolean
}

export interface UserCreateRequest {
  login_id: string
  name: string
  department: string
  role: Role
  initial_password: string
}

export interface UserUpdateRequest {
  name: string
  department: string
  role: Role
  is_active: boolean
}

export interface PasswordResetRequest {
  new_password: string
}

export interface UserResponse {
  id: number
  login_id: string
  name: string
  department: string
  role: Role
  is_active: boolean
  must_change_password: boolean
  created_at: string
  updated_at: string
}

export interface UserListQuery extends PageQuery {
  keyword?: string
  role?: Role
  is_active?: boolean
}

// ---- 備品管理 ----

export interface EquipmentCreateRequest {
  asset_number: string
  name: string
  category: string
  description: string
  location: string
}

export interface EquipmentUpdateRequest {
  name: string
  category: string
  description: string
  location: string
  is_active: boolean
}

export interface EquipmentListQuery extends PageQuery {
  keyword?: string
  category?: string
  availability?: Availability
  include_inactive?: boolean
}

export interface EquipmentResponse {
  id: number
  asset_number: string
  name: string
  category: string
  description: string
  location: string
  is_active: boolean
  availability: Availability
  current_due_date: string | null
  is_overdue: boolean
  current_borrower_name: string | null
  created_at: string
  updated_at: string
}

export interface CategoryListResponse {
  items: string[]
}

export interface ReservationResponse {
  start_date: string
  due_date: string
  occupied_until: string
  status: 'approved' | 'lent'
  borrower_name: string | null
}

export interface ReservationListResponse {
  items: ReservationResponse[]
}

export interface CsvImportResponse {
  imported_count: number
}

export interface CsvRowError {
  row_number: number
  column: string | null
  message: string
}

// ---- 貸出申請・承認 ----

export interface LoanRequestCreateRequest {
  equipment_id: number
  start_date: string
  due_date: string
  purpose: string
}

export interface LoanRequestReasonRequest {
  reason: string
}

export interface LoanRequestListQuery extends PageQuery {
  status?: LoanStatus
}

export interface LoanRequestResponse {
  id: number
  equipment_id: number
  equipment_asset_number: string
  equipment_name: string
  requester_id: number
  requester_name: string
  requester_department: string
  start_date: string
  due_date: string
  purpose: string
  status: LoanStatus
  reason: string
  return_note: string
  requested_at: string
  decided_at: string | null
  lent_at: string | null
  returned_at: string | null
  canceled_at: string | null
  is_overdue: boolean
  overdue_days: number
}

// ---- 貸出・返却・履歴 ----

export interface LoanReturnRequest {
  return_note?: string
}

export interface LoanHistoryFilter {
  from_date?: string
  to_date?: string
  equipment_id?: number
  requester_id?: number
}

export interface LoanHistoryQuery extends LoanHistoryFilter, PageQuery {}

export interface LoanHistoryResponse {
  id: number
  equipment_id: number
  equipment_asset_number: string
  equipment_name: string
  requester_id: number
  requester_name: string
  requester_department: string
  start_date: string
  due_date: string
  purpose: string
  status: LoanStatus
  lent_at: string
  returned_at: string | null
  return_note: string
  delay_days: number
}

// ---- 通知 ----

export interface NotificationListQuery extends PageQuery {
  is_read?: boolean
}

export interface NotificationResponse {
  id: number
  type: NotificationType
  loan_request_id: number
  equipment_asset_number: string
  equipment_name: string
  is_read: boolean
  created_at: string
  notified_date: string
}

export interface NotificationSummaryResponse {
  unread_count: number
  pending_request_count: number | null
}

export interface NotificationReadAllResponse {
  updated_count: number
}
