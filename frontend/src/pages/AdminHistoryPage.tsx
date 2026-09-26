/**
 * 貸出履歴画面（S11）
 *
 * 【概要】
 * 期間・備品・借用者で絞り込んで貸出履歴を表示し、同じ条件でCSVを出力する。
 * 終了日は開始日以降でなければならない。備品・借用者の選択肢は先頭100件から選ぶ。
 */

import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { fetchUsers } from '../api/adminUsers'
import { saveBlob } from '../api/client'
import { fetchEquipments } from '../api/equipments'
import { downloadLoanHistoryCsv, fetchLoanHistory } from '../api/loanHistory'
import { ErrorMessage } from '../components/ErrorMessage'
import { FormField } from '../components/FormField'
import { Loading } from '../components/Loading'
import { Pagination } from '../components/Pagination'
import type { LoanHistoryFilter } from '../types/api'
import { toErrorMessage } from '../utils/error'
import { formatDate, formatDateTime, loanStatusLabel } from '../utils/format'
import { useFetch } from '../utils/useFetch'

const PAGE_SIZE = 20
const OPTION_LIMIT = 100

/** 検索条件の入力値を検証する（問題なければnull） */
function validateFilter(fromDate: string, toDate: string): string | null {
  if (fromDate !== '' && toDate !== '' && toDate < fromDate) {
    return '終了日は開始日以降の日付を指定してください'
  }
  return null
}

/** 選択値（文字列）を数値IDへ変換する（未選択はundefined） */
function toOptionalId(value: string): number | undefined {
  return value === '' ? undefined : Number(value)
}

export function AdminHistoryPage() {
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [equipmentId, setEquipmentId] = useState('')
  const [requesterId, setRequesterId] = useState('')
  const [filter, setFilter] = useState<LoanHistoryFilter>({})
  const [page, setPage] = useState(1)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const loadEquipmentOptions = useCallback(() => {
    return fetchEquipments({ include_inactive: true, page: 1, page_size: OPTION_LIMIT })
  }, [])
  const equipmentOptions = useFetch(loadEquipmentOptions)

  const loadUserOptions = useCallback(() => {
    return fetchUsers({ page: 1, page_size: OPTION_LIMIT })
  }, [])
  const userOptions = useFetch(loadUserOptions)

  const loadHistory = useCallback(() => {
    return fetchLoanHistory({ ...filter, page, page_size: PAGE_SIZE })
  }, [filter, page])
  const history = useFetch(loadHistory)

  /** 入力中の条件から検索条件を組み立てる */
  function buildFilter(): LoanHistoryFilter {
    return {
      from_date: fromDate === '' ? undefined : fromDate,
      to_date: toDate === '' ? undefined : toDate,
      equipment_id: toOptionalId(equipmentId),
      requester_id: toOptionalId(requesterId),
    }
  }

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = validateFilter(fromDate, toDate)
    setValidationError(message)
    if (message !== null) {
      return
    }
    const nextFilter = buildFilter()
    setFilter(nextFilter)
    setPage(1)
  }

  async function handleExport() {
    const message = validateFilter(fromDate, toDate)
    setValidationError(message)
    if (message !== null) {
      return
    }
    setExporting(true)
    setExportError(null)
    try {
      const nextFilter = buildFilter()
      const result = await downloadLoanHistoryCsv(nextFilter)
      saveBlob(result)
    } catch (error) {
      const errorText = toErrorMessage(error)
      setExportError(errorText)
    } finally {
      setExporting(false)
    }
  }

  return (
    <section>
      <h2>貸出履歴</h2>

      <form onSubmit={handleSearch} className="search-form">
        <FormField id="history-from-date" label="開始日（貸出日）">
          <input
            id="history-from-date"
            type="date"
            value={fromDate}
            onChange={(event) => setFromDate(event.target.value)}
          />
        </FormField>
        <FormField id="history-to-date" label="終了日（貸出日）">
          <input
            id="history-to-date"
            type="date"
            min={fromDate}
            value={toDate}
            onChange={(event) => setToDate(event.target.value)}
          />
        </FormField>
        <FormField id="history-equipment" label="備品">
          <select
            id="history-equipment"
            value={equipmentId}
            onChange={(event) => setEquipmentId(event.target.value)}
          >
            <option value="">すべて</option>
            {(equipmentOptions.data?.items ?? []).map((equipment) => (
              <option key={equipment.id} value={equipment.id}>
                {equipment.asset_number} {equipment.name}
              </option>
            ))}
          </select>
        </FormField>
        <FormField id="history-requester" label="借用者">
          <select
            id="history-requester"
            value={requesterId}
            onChange={(event) => setRequesterId(event.target.value)}
          >
            <option value="">すべて</option>
            {(userOptions.data?.items ?? []).map((user) => (
              <option key={user.id} value={user.id}>
                {user.name}（{user.login_id}）
              </option>
            ))}
          </select>
        </FormField>
        <button type="submit">検索</button>
        <button type="button" className="secondary" onClick={handleExport} disabled={exporting}>
          {exporting ? '出力中…' : 'CSV出力'}
        </button>
      </form>
      <ErrorMessage message={validationError} />
      <ErrorMessage message={exportError} />
      <ErrorMessage message={equipmentOptions.error} />
      <ErrorMessage message={userOptions.error} />

      {history.status === 'loading' ? <Loading /> : null}
      {history.status === 'error' ? (
        <>
          <ErrorMessage message={history.error} />
          <button type="button" onClick={history.reload}>
            再読み込み
          </button>
        </>
      ) : null}
      {history.status === 'success' && history.data !== null ? (
        <>
          {history.data.items.length === 0 ? (
            <p>該当する貸出履歴はありません。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>備品</th>
                  <th>借用者</th>
                  <th>期間</th>
                  <th>貸出日時</th>
                  <th>返却日時</th>
                  <th>状態</th>
                  <th>遅延日数</th>
                  <th>返却時メモ</th>
                </tr>
              </thead>
              <tbody>
                {history.data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      {item.equipment_asset_number} {item.equipment_name}
                    </td>
                    <td>
                      {item.requester_name}（{item.requester_department}）
                    </td>
                    <td>
                      {formatDate(item.start_date)}〜{formatDate(item.due_date)}
                    </td>
                    <td>{formatDateTime(item.lent_at)}</td>
                    <td>{formatDateTime(item.returned_at)}</td>
                    <td>{loanStatusLabel(item.status)}</td>
                    <td>{item.delay_days}日</td>
                    <td>{item.return_note === '' ? '-' : item.return_note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pagination
            page={history.data.page}
            pageSize={history.data.page_size}
            total={history.data.total}
            onChange={setPage}
          />
        </>
      ) : null}
    </section>
  )
}
