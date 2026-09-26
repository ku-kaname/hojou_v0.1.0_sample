/**
 * ルートガード（ログイン必須・管理者専用）
 *
 * 【概要】
 * RequireAuth：未ログインならログイン画面へ、初期パスワード変更が必要ならパスワード変更画面へ移動させる。
 * RequireAdmin：管理者以外はホーム画面へ移動させる。
 * PasswordChangeRoute：ログイン済みであれば初期パスワード変更中でも表示できる（パスワード変更画面用）。
 * 画面側のガードは利便のための制御であり、権限の最終判定はバックエンドが行う。
 */

import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Loading } from '../components/Loading'
import { useAuth } from './useAuth'

/** ログイン必須（初期パスワード変更が必要な間は、パスワード変更画面以外へ移動できない） */
export function RequireAuth() {
  const { status, user } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return <Loading />
  }
  if (status === 'unauthenticated' || user === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  if (user.must_change_password) {
    return <Navigate to="/password-change" replace />
  }
  return <Outlet />
}

/** ログイン必須（初期パスワード変更が必要な場合でも表示できる） */
export function PasswordChangeRoute() {
  const { status, user } = useAuth()

  if (status === 'loading') {
    return <Loading />
  }
  if (status === 'unauthenticated' || user === null) {
    return <Navigate to="/login" replace />
  }
  return <Outlet />
}

/** 管理者専用 */
export function RequireAdmin() {
  const { user } = useAuth()

  if (user === null || user.role !== 'admin') {
    return <Navigate to="/" replace />
  }
  return <Outlet />
}
