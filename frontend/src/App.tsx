/**
 * ルーティング定義
 *
 * 【概要】
 * 画面一覧（S01〜S11）のパスと、ログイン必須・管理者専用のガードを定義する。
 * 未定義のパスはホーム画面へ移動させる。
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { PasswordChangeRoute, RequireAdmin, RequireAuth } from './auth/RouteGuards'
import { Layout } from './components/Layout'
import { PlaceholderPage } from './pages/PlaceholderPage'

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<PlaceholderPage title="S01 ログイン" />} />

      <Route element={<PasswordChangeRoute />}>
        <Route element={<Layout />}>
          <Route path="/password-change" element={<PlaceholderPage title="S06 パスワード変更" />} />
        </Route>
      </Route>

      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route path="/" element={<PlaceholderPage title="S02 ホーム" />} />
          <Route path="/equipments" element={<PlaceholderPage title="S03 備品一覧" />} />
          <Route
            path="/equipments/:equipmentId"
            element={<PlaceholderPage title="S04 備品詳細・貸出申請" />}
          />
          <Route path="/my-requests" element={<PlaceholderPage title="S05 自分の申請一覧" />} />

          <Route element={<RequireAdmin />}>
            <Route path="/admin/requests" element={<PlaceholderPage title="S07 申請管理" />} />
            <Route path="/admin/overdue" element={<PlaceholderPage title="S08 期限超過一覧" />} />
            <Route path="/admin/equipments" element={<PlaceholderPage title="S09 備品管理" />} />
            <Route path="/admin/users" element={<PlaceholderPage title="S10 ユーザー管理" />} />
            <Route path="/admin/history" element={<PlaceholderPage title="S11 貸出履歴" />} />
          </Route>
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
