/**
 * 通知API
 *
 * 【概要】
 * 通知サマリー（ヘッダーのバッジ用）・通知一覧・既読化のAPIを呼び出す。
 */

import type {
  NotificationListQuery,
  NotificationReadAllResponse,
  NotificationResponse,
  NotificationSummaryResponse,
  Page,
} from '../types/api'
import { apiGet, apiPost } from './client'

/** 未読通知件数（管理者は承認待ち件数を含む）を取得する */
export function fetchNotificationSummary(): Promise<NotificationSummaryResponse> {
  return apiGet<NotificationSummaryResponse>('/notifications/summary')
}

/** 自分宛の通知を新しい順に取得する（既読・未読で絞り込める） */
export function fetchNotifications(
  query: NotificationListQuery,
): Promise<Page<NotificationResponse>> {
  return apiGet<Page<NotificationResponse>>('/notifications', { ...query })
}

/** 通知を既読にする */
export function markNotificationRead(notificationId: number): Promise<void> {
  return apiPost<void>(`/notifications/${notificationId}/read`)
}

/** 自分宛の未読通知をすべて既読にする */
export function markAllNotificationsRead(): Promise<NotificationReadAllResponse> {
  return apiPost<NotificationReadAllResponse>('/notifications/read-all')
}
