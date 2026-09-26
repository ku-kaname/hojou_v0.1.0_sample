/**
 * 表示用の整形・状態ラベル
 *
 * 【概要】
 * APIから受け取った日付・日時・状態値を、画面表示用の日本語表記へ変換する。
 */

import type { Availability, LoanStatus, NotificationType, Role } from '../types/api'

/** 申請状態の表示名 */
export const LOAN_STATUS_LABELS: Record<LoanStatus, string> = {
  requested: '承認待ち',
  approved: '承認済み',
  lent: '貸出中',
  returned: '返却済み',
  rejected: '却下',
  canceled: '取消',
}

/** 通知種別の表示名 */
export const NOTIFICATION_TYPE_LABELS: Record<NotificationType, string> = {
  approved: '申請が承認されました',
  rejected: '申請が却下されました',
  canceled: '申請が取り消されました',
  new_request: '新しい貸出申請があります',
  due_soon: '返却期限が近づいています',
  overdue: '返却期限を過ぎています',
}

/** 貸出状況の表示名 */
export const AVAILABILITY_LABELS: Record<Availability, string> = {
  available: '貸出可',
  lent: '貸出中',
}

/** ロールの表示名 */
export const ROLE_LABELS: Record<Role, string> = {
  general: '一般ユーザー',
  admin: '管理者',
}

/** 日付（YYYY-MM-DD）を「YYYY/MM/DD」表記にする（未指定は「-」） */
export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return '-'
  }
  return value.slice(0, 10).replaceAll('-', '/')
}

/** 日時（ISO 8601）を「YYYY/MM/DD HH:MM」表記にする。APIはJSTで返すため時刻部分をそのまま使う */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return '-'
  }
  const datePart = formatDate(value)
  const timePart = value.slice(11, 16)
  return timePart === '' ? datePart : `${datePart} ${timePart}`
}

/** 日付を入力欄・APIで使う「YYYY-MM-DD」形式にする（ローカル日付基準） */
export function toDateInputValue(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/** 申請状態の表示名を返す */
export function loanStatusLabel(status: LoanStatus): string {
  return LOAN_STATUS_LABELS[status]
}

/** 取消できる申請状態か（申請中・承認済みのみ） */
export function isCancelable(status: LoanStatus): boolean {
  return status === 'requested' || status === 'approved'
}

/** 通知の移動先画面を返す（新規申請は管理者の申請管理、期限超過は管理者なら期限超過一覧、それ以外は自分の申請一覧） */
export function notificationLink(type: NotificationType, isAdmin: boolean): string {
  if (type === 'new_request') {
    return '/admin/requests'
  }
  if (type === 'overdue' && isAdmin) {
    return '/admin/overdue'
  }
  return '/my-requests'
}
