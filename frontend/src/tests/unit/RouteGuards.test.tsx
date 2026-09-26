import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthContext } from '../../auth/authContext'
import type { AuthContextValue } from '../../auth/authContext'
import { RequireAdmin, RequireAuth } from '../../auth/RouteGuards'
import type { MeResponse } from '../../types/api'

function makeAuth(user: MeResponse | null): AuthContextValue {
  return {
    status: user === null ? 'unauthenticated' : 'authenticated',
    user,
    signIn: vi.fn(),
    signOut: vi.fn(),
    replaceToken: vi.fn(),
  }
}

function makeUser(overrides: Partial<MeResponse>): MeResponse {
  return {
    id: 1,
    login_id: 'user1',
    name: '利用者',
    department: '総務',
    role: 'general',
    must_change_password: false,
    ...overrides,
  }
}

function renderRoutes(auth: AuthContextValue, path: string) {
  return render(
    <AuthContext.Provider value={auth}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/login" element={<p>ログイン画面</p>} />
          <Route path="/password-change" element={<p>パスワード変更画面</p>} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<p>ホーム画面</p>} />
            <Route element={<RequireAdmin />}>
              <Route path="/admin" element={<p>管理画面</p>} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('ルートガード', () => {
  it('未ログインならログイン画面へ移動する', () => {
    renderRoutes(makeAuth(null), '/')
    expect(screen.getByText('ログイン画面')).toBeInTheDocument()
  })

  it('初期パスワード変更が必要ならパスワード変更画面へ移動する', () => {
    renderRoutes(makeAuth(makeUser({ must_change_password: true })), '/')
    expect(screen.getByText('パスワード変更画面')).toBeInTheDocument()
  })

  it('一般ユーザーは管理画面を表示できない', () => {
    renderRoutes(makeAuth(makeUser({})), '/admin')
    expect(screen.getByText('ホーム画面')).toBeInTheDocument()
  })

  it('管理者は管理画面を表示できる', () => {
    renderRoutes(makeAuth(makeUser({ role: 'admin' })), '/admin')
    expect(screen.getByText('管理画面')).toBeInTheDocument()
  })
})
