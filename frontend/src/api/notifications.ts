/**
 * 通知API（共通利用分）
 *
 * 【概要】
 * ヘッダーの通知バッジ表示に使う通知サマリーを取得する。
 */

import type { NotificationSummaryResponse } from '../types/api'
import { apiGet } from './client'

/** 未読通知件数（管理者は承認待ち件数を含む）を取得する */
export function fetchNotificationSummary(): Promise<NotificationSummaryResponse> {
  return apiGet<NotificationSummaryResponse>('/notifications/summary')
}
