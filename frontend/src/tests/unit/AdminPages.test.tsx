/**
 * 単体テスト：管理者画面（申請管理・備品管理）
 *
 * テスト仕様書：単体テスト仕様書/frontend/画面/認証・管理者画面（項番8〜11・15・16）
 * 設計書：要件定義書/画面一覧（S07 申請管理・S09 備品管理）
 * テスト対象ファイル：frontend/src/pages/AdminRequestsPage.tsx、AdminEquipmentsPage.tsx
 *
 * 【テストの考え方】
 * サーバーには接続せず、通信（fetch）を偽物（モック）に差し替えた状態で画面を表示し、
 * 利用者と同じ操作（ボタンを押す・入力する）をして、表示や通信の有無を確認する。
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { AdminEquipmentsPage } from '../../pages/AdminEquipmentsPage'
import { AdminRequestsPage } from '../../pages/AdminRequestsPage'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const requestedItem = {
  id: 7,
  equipment_id: 1,
  equipment_asset_number: 'A-001',
  equipment_name: 'ノートPC',
  requester_id: 2,
  requester_name: '利用者',
  requester_department: '総務',
  start_date: '2026-10-01',
  due_date: '2026-10-03',
  purpose: '出張',
  status: 'requested',
  reason: '',
  return_note: '',
  requested_at: '2026-09-26T10:00:00+09:00',
  is_overdue: false,
  overdue_days: 0,
}

function pageOf(items: unknown[]) {
  return { items, total: items.length, page: 1, page_size: 20 }
}

describe('申請管理画面', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  /** 却下APIの呼び出し回数を数える */
  function countRejectCalls(fetchMock: ReturnType<typeof vi.spyOn>): number {
    const calls = fetchMock.mock.calls.filter((call) => String(call[0]).includes('/reject'))
    return calls.length
  }

  /** 却下できる状態（承認待ち1件）の一覧を表示する偽物の通信を用意して、画面を表示する */
  function renderWithRejectableRequest() {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/reject')) {
        return Promise.resolve(jsonResponse({ ...requestedItem, status: 'rejected' }))
      }
      return Promise.resolve(jsonResponse(pageOf([requestedItem])))
    })
    render(
      <MemoryRouter>
        <AdminRequestsPage />
      </MemoryRouter>,
    )
    return fetchMock
  }

  it('項番8：却下理由が空のまま「却下する」を押すとエラーを表示し、却下APIを呼ばない', async () => {
    const fetchMock = renderWithRejectableRequest()
    await userEvent.click(await screen.findByRole('button', { name: '却下' }))
    await userEvent.click(screen.getByRole('button', { name: '却下する' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('却下理由を入力してください')
    expect(countRejectCalls(fetchMock)).toBe(0)
  })

  it('項番9：却下理由を入力して「却下する」を押すと、却下APIを1回呼んで完了メッセージを表示する', async () => {
    const fetchMock = renderWithRejectableRequest()
    await userEvent.click(await screen.findByRole('button', { name: '却下' }))
    await userEvent.type(screen.getByLabelText(/却下理由/), '在庫なし')
    await userEvent.click(screen.getByRole('button', { name: '却下する' }))
    await waitFor(() => {
      expect(countRejectCalls(fetchMock)).toBe(1)
    })
    expect(await screen.findByRole('status')).toHaveTextContent('申請を却下しました')
  })

  it('項番10：承認は確認ダイアログでキャンセルすると、承認APIを呼ばない', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(pageOf([requestedItem])))
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(
      <MemoryRouter>
        <AdminRequestsPage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '承認' }))
    const approveCalls = fetchMock.mock.calls.filter((call) => String(call[0]).includes('/approve'))
    expect(approveCalls).toHaveLength(0)
  })

  it('項番11：状態の絞り込みを変更すると、前回の操作結果メッセージが消える', async () => {
    renderWithRejectableRequest()
    await userEvent.click(await screen.findByRole('button', { name: '却下' }))
    await userEvent.type(screen.getByLabelText(/却下理由/), '在庫なし')
    await userEvent.click(screen.getByRole('button', { name: '却下する' }))
    expect(await screen.findByRole('status')).toHaveTextContent('申請を却下しました')

    // 状態を切り替える（「状態」の選択欄を「承認済み」へ）
    await userEvent.selectOptions(screen.getByLabelText('状態'), 'approved')
    await waitFor(() => {
      expect(screen.queryByRole('status')).toBeNull()
    })
  })
})

describe('備品管理画面', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('項番15：CSV一括登録の誤りを、行番号・列・内容の表で表示する', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/admin/equipments/import')) {
        return Promise.resolve(
          jsonResponse(
            {
              detail: 'CSVに誤りがあります',
              errors: [{ row_number: 3, column: '資産番号', message: '資産番号が重複しています' }],
            },
            400,
          ),
        )
      }
      return Promise.resolve(jsonResponse(pageOf([])))
    })
    render(
      <MemoryRouter>
        <AdminEquipmentsPage />
      </MemoryRouter>,
    )
    const file = new File(['資産番号,備品名'], 'equipments.csv', { type: 'text/csv' })
    await userEvent.upload(screen.getByLabelText('CSVファイル'), file)
    await userEvent.click(screen.getByRole('button', { name: '一括登録する' }))
    const cell = await screen.findByText('資産番号が重複しています')
    const row = cell.closest('tr')
    expect(row).not.toBeNull()
    if (row !== null) {
      expect(within(row).getByText('3')).toBeInTheDocument()
    }
  })

  it('項番16：編集時は資産番号の入力欄が編集不可になる', async () => {
    const equipment = {
      id: 1,
      asset_number: 'A-001',
      name: 'ノートPC',
      category: 'PC',
      description: '',
      location: '',
      is_active: true,
      availability: 'available',
      current_due_date: null,
      is_overdue: false,
      current_borrower_name: null,
      created_at: '2026-09-01T00:00:00+09:00',
      updated_at: '2026-09-01T00:00:00+09:00',
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(pageOf([equipment])))
    render(
      <MemoryRouter>
        <AdminEquipmentsPage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '編集' }))
    expect(screen.getByLabelText(/資産番号/)).toBeDisabled()
  })
})
