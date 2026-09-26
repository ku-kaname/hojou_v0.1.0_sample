/**
 * ホーム画面（S02）
 *
 * 【概要】
 * 未読の通知、自分の貸出中の備品（期限超過を含む）を表示する。
 * 管理者には、承認待ちの申請件数と期限超過の件数も表示する。
 * 通知は1件ずつ、またはまとめて既読にできる。
 */

import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchOverdueLoanRequests, fetchMyLoanRequests } from '../api/loanRequests'
import {
  fetchNotificationSummary,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '../api/notifications'
import { useAuth } from '../auth/useAuth'
import { ErrorMessage } from '../components/ErrorMessage'
import { Loading } from '../components/Loading'
import { StatusBadge } from '../components/StatusBadge'
import type { NotificationResponse } from '../types/api'
import { toErrorMessage } from '../utils/error'
import { notifyNotificationsChanged } from '../utils/notificationEvents'
import { NOTIFICATION_TYPE_LABELS, formatDate, notificationLink } from '../utils/format'
import { useFetch } from '../utils/useFetch'

/** 一覧に表示する未読通知の最大件数 */
const NOTIFICATION_LIMIT = 10

export function HomePage() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [actionError, setActionError] = useState<string | null>(null)

  const loadNotifications = useCallback(() => {
    return fetchNotifications({ is_read: false, page: 1, page_size: NOTIFICATION_LIMIT })
  }, [])
  const notifications = useFetch(loadNotifications)

  const summary = useFetch(fetchNotificationSummary)

  const loadLent = useCallback(() => {
    return fetchMyLoanRequests({ status: 'lent', page: 1, page_size: 100 })
  }, [])
  const lent = useFetch(loadLent)

  const loadOverdue = useCallback(() => {
    if (!isAdmin) {
      return Promise.resolve(null)
    }
    return fetchOverdueLoanRequests({ page: 1, page_size: 1 })
  }, [isAdmin])
  const overdue = useFetch(loadOverdue)

  function reloadNotifications() {
    notifications.reload()
    summary.reload()
    notifyNotificationsChanged()
  }

  async function handleRead(notification: NotificationResponse) {
    setActionError(null)
    try {
      await markNotificationRead(notification.id)
      reloadNotifications()
    } catch (error) {
      setActionError(toErrorMessage(error))
    }
  }

  async function handleReadAll() {
    setActionError(null)
    try {
      await markAllNotificationsRead()
      reloadNotifications()
    } catch (error) {
      setActionError(toErrorMessage(error))
    }
  }

  return (
    <section>
      <h2>ホーム</h2>

      {isAdmin ? (
        <div className="card">
          <h3>管理者向け</h3>
          {summary.status === 'success' && overdue.status === 'success' ? (
            <ul>
              <li>
                承認待ちの申請：{summary.data?.pending_request_count ?? 0}件（
                <Link to="/admin/requests">申請管理へ</Link>）
              </li>
              <li>
                期限超過の貸出：{overdue.data?.total ?? 0}件（
                <Link to="/admin/overdue">期限超過一覧へ</Link>）
              </li>
            </ul>
          ) : null}
          {summary.status === 'loading' || overdue.status === 'loading' ? <Loading /> : null}
          {summary.status === 'error' || overdue.status === 'error' ? (
            <ErrorMessage message={summary.error ?? overdue.error} />
          ) : null}
        </div>
      ) : null}

      <div className="card">
        <h3>未読の通知</h3>
        <ErrorMessage message={actionError} />
        {notifications.status === 'loading' ? <Loading /> : null}
        {notifications.status === 'error' ? (
          <>
            <ErrorMessage message={notifications.error} />
            <button type="button" onClick={notifications.reload}>
              再読み込み
            </button>
          </>
        ) : null}
        {notifications.status === 'success' && notifications.data !== null ? (
          notifications.data.items.length === 0 ? (
            <p>未読の通知はありません。</p>
          ) : (
            <>
              <ul className="notification-list">
                {notifications.data.items.map((notification) => (
                  <li key={notification.id}>
                    <Link to={notificationLink(notification.type, isAdmin)}>
                      {NOTIFICATION_TYPE_LABELS[notification.type]}
                    </Link>
                    ：{notification.equipment_asset_number} {notification.equipment_name}（
                    {formatDate(notification.notified_date)}）
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => handleRead(notification)}
                    >
                      既読にする
                    </button>
                  </li>
                ))}
              </ul>
              <p>未読 全{notifications.data.total}件</p>
              <button type="button" onClick={handleReadAll}>
                すべて既読にする
              </button>
            </>
          )
        ) : null}
      </div>

      <div className="card">
        <h3>貸出中の備品</h3>
        {lent.status === 'loading' ? <Loading /> : null}
        {lent.status === 'error' ? (
          <>
            <ErrorMessage message={lent.error} />
            <button type="button" onClick={lent.reload}>
              再読み込み
            </button>
          </>
        ) : null}
        {lent.status === 'success' && lent.data !== null ? (
          lent.data.items.length === 0 ? (
            <p>現在、貸出中の備品はありません。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>備品</th>
                  <th>返却予定日</th>
                  <th>状態</th>
                </tr>
              </thead>
              <tbody>
                {lent.data.items.map((request) => (
                  <tr key={request.id}>
                    <td>
                      {request.equipment_asset_number} {request.equipment_name}
                    </td>
                    <td>{formatDate(request.due_date)}</td>
                    <td>
                      <StatusBadge status={request.status} overdue={request.is_overdue} />
                      {request.is_overdue ? <span>（超過{request.overdue_days}日）</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : null}
        <p>
          <Link to="/my-requests">自分の申請一覧へ</Link>
        </p>
      </div>
    </section>
  )
}
