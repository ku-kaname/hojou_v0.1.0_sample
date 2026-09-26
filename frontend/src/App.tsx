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
import { AdminRequestsPage } from './pages/AdminRequestsPage'
import { AdminOverduePage } from './pages/AdminOverduePage'
import { AdminEquipmentsPage } from './pages/AdminEquipmentsPage'
import { AdminUsersPage } from './pages/AdminUsersPage'
import { AdminHistoryPage } from './pages/AdminHistoryPage'

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
            <Route path="/admin/requests" element={<AdminRequestsPage />} />
            <Route path="/admin/overdue" element={<AdminOverduePage />} />
            <Route path="/admin/equipments" element={<AdminEquipmentsPage />} />
            <Route path="/admin/users" element={<AdminUsersPage />} />
            <Route path="/admin/history" element={<AdminHistoryPage />} />
          </Route>
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
