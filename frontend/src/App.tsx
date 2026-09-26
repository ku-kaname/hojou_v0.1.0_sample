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
import { LoginPage } from './pages/LoginPage'
import { HomePage } from './pages/HomePage'
import { EquipmentListPage } from './pages/EquipmentListPage'
import { EquipmentDetailPage } from './pages/EquipmentDetailPage'
import { MyRequestsPage } from './pages/MyRequestsPage'
import { PasswordChangePage } from './pages/PasswordChangePage'
import { PlaceholderPage } from './pages/PlaceholderPage'

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<PasswordChangeRoute />}>
        <Route element={<Layout />}>
          <Route path="/password-change" element={<PasswordChangePage />} />
        </Route>
      </Route>

      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route path="/" element={<HomePage />} />
          <Route path="/equipments" element={<EquipmentListPage />} />
          <Route path="/equipments/:equipmentId" element={<EquipmentDetailPage />} />
          <Route path="/my-requests" element={<MyRequestsPage />} />

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
