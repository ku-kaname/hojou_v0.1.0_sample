/**
 * 自分の申請一覧画面（S05）
 *
 * 【概要】
 * 自分の貸出申請を新しい順に一覧表示し、状態で絞り込む。
 * 申請中・承認済みの申請は、確認の上で取り消せる。
 */

import { useCallback, useState } from 'react'
import { cancelLoanRequest, fetchMyLoanRequests } from '../api/loanRequests'
import { ErrorMessage } from '../components/ErrorMessage'
import { Loading } from '../components/Loading'
import { Pagination } from '../components/Pagination'
import { StatusBadge } from '../components/StatusBadge'
import type { LoanRequestResponse, LoanStatus } from '../types/api'
import { toErrorMessage } from '../utils/error'
import { LOAN_STATUS_LABELS, formatDate, formatDateTime, isCancelable } from '../utils/format'
import { useFetch } from '../utils/useFetch'

const PAGE_SIZE = 20
const STATUS_OPTIONS = Object.keys(LOAN_STATUS_LABELS) as LoanStatus[]

export function MyRequestsPage() {
  const [status, setStatus] = useState<'' | LoanStatus>('')
  const [page, setPage] = useState(1)
  const [actionError, setActionError] = useState<string | null>(null)
  const [cancelingId, setCancelingId] = useState<number | null>(null)

  const loadRequests = useCallback(() => {
    return fetchMyLoanRequests({
      status: status === '' ? undefined : status,
      page,
      page_size: PAGE_SIZE,
    })
  }, [status, page])
  const requests = useFetch(loadRequests)

  function handleStatusChange(value: string) {
    setStatus(value as '' | LoanStatus)
    setPage(1)
  }

  async function handleCancel(request: LoanRequestResponse) {
    const confirmed = window.confirm(
      `「${request.equipment_name}」の申請を取り消します。よろしいですか？`,
    )
    if (!confirmed) {
      return
    }
    setCancelingId(request.id)
    setActionError(null)
    try {
      await cancelLoanRequest(request.id)
      requests.reload()
    } catch (error) {
      setActionError(toErrorMessage(error))
    } finally {
      setCancelingId(null)
    }
  }

  return (
    <section>
      <h2>自分の申請一覧</h2>
      <div className="form-field">
        <label htmlFor="filter-status">状態</label>
        <select
          id="filter-status"
          value={status}
          onChange={(event) => handleStatusChange(event.target.value)}
        >
          <option value="">すべて</option>
          {STATUS_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {LOAN_STATUS_LABELS[option]}
            </option>
          ))}
        </select>
      </div>

      <ErrorMessage message={actionError} />
      {requests.status === 'loading' ? <Loading /> : null}
      {requests.status === 'error' ? (
        <>
          <ErrorMessage message={requests.error} />
          <button type="button" onClick={requests.reload}>
            再読み込み
          </button>
        </>
      ) : null}
      {requests.status === 'success' && requests.data !== null ? (
        <>
          {requests.data.items.length === 0 ? (
            <p>申請はありません。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>備品</th>
                  <th>期間</th>
                  <th>用途</th>
                  <th>状態</th>
                  <th>備考</th>
                  <th>申請日時</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {requests.data.items.map((request) => (
                  <tr key={request.id}>
                    <td>
                      {request.equipment_asset_number} {request.equipment_name}
                    </td>
                    <td>
                      {formatDate(request.start_date)}〜{formatDate(request.due_date)}
                    </td>
                    <td>{request.purpose}</td>
                    <td>
                      <StatusBadge status={request.status} overdue={request.is_overdue} />
                      {request.is_overdue ? <div>超過{request.overdue_days}日</div> : null}
                    </td>
                    <td>{request.reason === '' ? request.return_note : request.reason}</td>
                    <td>{formatDateTime(request.requested_at)}</td>
                    <td>
                      {isCancelable(request.status) ? (
                        <button
                          type="button"
                          className="danger"
                          disabled={cancelingId === request.id}
                          onClick={() => handleCancel(request)}
                        >
                          取消
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pagination
            page={requests.data.page}
            pageSize={requests.data.page_size}
            total={requests.data.total}
            onChange={setPage}
          />
        </>
      ) : null}
    </section>
  )
}
