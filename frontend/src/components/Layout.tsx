/**
 * 共通レイアウト（ヘッダー・ナビゲーション）
 *
 * 【概要】
 * ログイン後の全画面に共通するヘッダーを表示する。
 * ロールに応じたメニュー、未読通知件数（管理者は承認待ち件数を含む）、ユーザー名、ログアウトを備える。
 * 画面遷移のたびに通知サマリーを取得し直す。
 */

import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { fetchNotificationSummary } from '../api/notifications'
import { useAuth } from '../auth/useAuth'
import type { NotificationSummaryResponse } from '../types/api'
import { subscribeNotificationsChanged } from '../utils/notificationEvents'

interface MenuItem {
  to: string
  label: string
}

const GENERAL_MENU: MenuItem[] = [
  { to: '/', label: 'ホーム' },
  { to: '/equipments', label: '備品一覧' },
  { to: '/my-requests', label: '自分の申請' },
  { to: '/password-change', label: 'パスワード変更' },
]

const ADMIN_MENU: MenuItem[] = [
  { to: '/admin/requests', label: '申請管理' },
  { to: '/admin/overdue', label: '期限超過' },
  { to: '/admin/equipments', label: '備品管理' },
  { to: '/admin/users', label: 'ユーザー管理' },
  { to: '/admin/history', label: '貸出履歴' },
]

export function Layout() {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [summary, setSummary] = useState<NotificationSummaryResponse | null>(null)

  const [refreshToken, setRefreshToken] = useState(0)
  const mustChangePassword = user?.must_change_password ?? false

  // 画面遷移のたびに通知サマリーを取得する（取得に失敗した場合はバッジを出さない）
  // 初期パスワード変更が必要な間は、APIが利用できないため取得しない
  useEffect(() => {
    if (mustChangePassword) {
      return
    }
    let cancelled = false
    async function load() {
      try {
        const data = await fetchNotificationSummary()
        if (!cancelled) {
          setSummary(data)
        }
      } catch {
        if (!cancelled) {
          setSummary(null)
        }
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [location.pathname, mustChangePassword, refreshToken])

  // 通知が既読になった場合も、未読件数を取得し直す
  useEffect(() => {
    function handleChanged() {
      setRefreshToken((current) => current + 1)
    }
    const unsubscribe = subscribeNotificationsChanged(handleChanged)
    return unsubscribe
  }, [])

  function handleSignOut() {
    signOut()
    navigate('/login', { replace: true })
  }

  const isAdmin = user?.role === 'admin'
  const menu = isAdmin ? [...GENERAL_MENU, ...ADMIN_MENU] : GENERAL_MENU
  const unreadCount = summary?.unread_count ?? 0
  const pendingCount = summary?.pending_request_count ?? 0

  return (
    <div className="app">
      <header className="app-header">
        <h1 className="app-title">社内備品貸出管理システム</h1>
        {mustChangePassword ? null : (
          <nav aria-label="メインメニュー">
            <ul className="nav-list">
              {menu.map((item) => (
                <li key={item.to}>
                  <NavLink to={item.to} end={item.to === '/'}>
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        )}
        <div className="header-user">
          {mustChangePassword ? null : (
            <span className="header-badges">
              <span className="badge badge-notice" title="未読の通知">
                通知 {unreadCount}
              </span>
              {isAdmin ? (
                <span className="badge badge-notice" title="承認待ちの申請">
                  承認待ち {pendingCount}
                </span>
              ) : null}
            </span>
          )}
          <span className="user-name">{user?.name}</span>
          <button type="button" onClick={handleSignOut}>
            ログアウト
          </button>
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
