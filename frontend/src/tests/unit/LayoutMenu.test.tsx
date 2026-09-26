/**
 * 単体テスト：共通レイアウト（ヘッダーのメニュー・通知バッジ）
 *
 * テスト仕様書：単体テスト仕様書/frontend/画面/認証・管理者画面（項番12〜14）
 * 設計書：要件定義書/画面一覧（共通ヘッダー）
 * テスト対象ファイル：frontend/src/components/Layout.tsx
 *
 * 【テストの考え方】
 * ログイン中のユーザー（権限・初期パスワード変更の要否）を差し替えて画面を表示し、
 * メニューと通知バッジが表示されるか、通知サマリーの通信が行われるかを確認する。
 * サーバーには接続せず、通信（fetch）は偽物（モック）に差し替える。
 */

import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthContext } from '../../auth/authContext'
import type { AuthContextValue } from '../../auth/authContext'
import { Layout } from '../../components/Layout'
import type { MeResponse } from '../../types/api'

function makeAuth(overrides: Partial<MeResponse>): AuthContextValue {
  const user: MeResponse = {
    id: 1,
    login_id: 'user1',
    name: '利用者',
    department: '総務',
    role: 'general',
    must_change_password: false,
    ...overrides,
  }
  return {
    status: 'authenticated',
    user,
    signIn: vi.fn(),
    signOut: vi.fn(),
    replaceToken: vi.fn(),
  }
}

function renderLayout(auth: AuthContextValue) {
  return render(
    <AuthContext.Provider value={auth}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<p>ホーム画面</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

/** 通知サマリー（未読3件・承認待ち2件）を返す偽物の通信を用意する */
function mockSummaryFetch() {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ unread_count: 3, pending_request_count: 2 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

describe('共通レイアウト', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('項番12：一般ユーザーには一般メニューと通知バッジを表示し、管理者メニュー・承認待ちバッジは表示しない', async () => {
    mockSummaryFetch()
    renderLayout(makeAuth({ role: 'general' }))
    expect(screen.getByRole('link', { name: '備品一覧' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '申請管理' })).toBeNull()
    expect(await screen.findByText('通知 3')).toBeInTheDocument()
    expect(screen.queryByText(/承認待ち/)).toBeNull()
  })

  it('項番13：管理者には一般メニューに加えて管理者メニューと承認待ちバッジを表示する', async () => {
    mockSummaryFetch()
    renderLayout(makeAuth({ role: 'admin' }))
    expect(screen.getByRole('link', { name: '備品一覧' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '申請管理' })).toBeInTheDocument()
    expect(await screen.findByText('承認待ち 2')).toBeInTheDocument()
  })

  it('項番14：初期パスワード変更が必要な間は、メニュー・バッジを出さず、通知サマリーも取得しない', async () => {
    const fetchMock = mockSummaryFetch()
    renderLayout(makeAuth({ must_change_password: true }))
    // ユーザー名とログアウトは表示する
    expect(screen.getByText('利用者')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ログアウト' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation')).toBeNull()
    expect(screen.queryByText(/通知 /)).toBeNull()
    // 通信が行われないことを、少し待ってから確認する
    await waitFor(() => {
      expect(fetchMock).not.toHaveBeenCalled()
    })
  })
})
