import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from '../../auth/AuthProvider'
import { LoginPage } from '../../pages/LoginPage'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<p>ホーム画面</p>} />
          <Route path="/password-change" element={<p>パスワード変更画面</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

const me = {
  id: 1,
  login_id: 'user1',
  name: '利用者',
  department: '総務',
  role: 'general',
  must_change_password: false,
}

describe('ログイン画面', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('ログイン失敗時はサーバーのメッセージを表示する', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ detail: 'ユーザーIDまたはパスワードが正しくありません' }, 401),
    )
    renderLogin()
    await userEvent.type(screen.getByLabelText(/ユーザーID/), 'user1')
    await userEvent.type(screen.getByLabelText(/パスワード/), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'ログイン' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'ユーザーIDまたはパスワードが正しくありません',
    )
  })

  it('ログイン成功時はホーム画面へ移動する', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/auth/login')) {
        return Promise.resolve(
          jsonResponse({
            access_token: 'token',
            token_type: 'bearer',
            must_change_password: false,
          }),
        )
      }
      return Promise.resolve(jsonResponse(me))
    })
    renderLogin()
    await userEvent.type(screen.getByLabelText(/ユーザーID/), 'user1')
    await userEvent.type(screen.getByLabelText(/パスワード/), 'correct-password')
    await userEvent.click(screen.getByRole('button', { name: 'ログイン' }))
    await waitFor(() => expect(screen.getByText('ホーム画面')).toBeInTheDocument())
  })

  it('初期パスワード変更が必要ならパスワード変更画面へ移動する', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/auth/login')) {
        return Promise.resolve(
          jsonResponse({ access_token: 'token', token_type: 'bearer', must_change_password: true }),
        )
      }
      return Promise.resolve(jsonResponse({ ...me, must_change_password: true }))
    })
    renderLogin()
    await userEvent.type(screen.getByLabelText(/ユーザーID/), 'user1')
    await userEvent.type(screen.getByLabelText(/パスワード/), 'initial-password')
    await userEvent.click(screen.getByRole('button', { name: 'ログイン' }))
    await waitFor(() => expect(screen.getByText('パスワード変更画面')).toBeInTheDocument())
  })
})
