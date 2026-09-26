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

  it('却下は理由が空だと送信されず、入力すると却下APIを呼ぶ', async () => {
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
    await userEvent.click(await screen.findByRole('button', { name: '却下' }))
    await userEvent.click(screen.getByRole('button', { name: '却下する' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('却下理由を入力してください')
    const rejectCalls = fetchMock.mock.calls.filter((call) => String(call[0]).includes('/reject'))
    expect(rejectCalls).toHaveLength(0)

    await userEvent.type(screen.getByLabelText(/却下理由/), '在庫なし')
    await userEvent.click(screen.getByRole('button', { name: '却下する' }))
    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter((call) => String(call[0]).includes('/reject'))
      expect(calls).toHaveLength(1)
    })
    expect(await screen.findByRole('status')).toHaveTextContent('申請を却下しました')
  })

  it('承認は確認をキャンセルするとAPIを呼ばない', async () => {
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
})

describe('備品管理画面', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('CSV一括登録の誤りを行ごとに表示する', async () => {
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

  it('編集時は資産番号を変更できない', async () => {
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
