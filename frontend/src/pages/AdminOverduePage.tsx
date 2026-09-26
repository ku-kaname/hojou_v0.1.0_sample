/**
 * 期限超過一覧画面（S08）
 *
 * 【概要】
 * 返却予定日を過ぎても返却されていない貸出を一覧表示する。
 * 返却の受付は「申請管理」画面の貸出中一覧で行う。
 */

import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchOverdueLoanRequests } from '../api/loanRequests'
import { ErrorMessage } from '../components/ErrorMessage'
import { Loading } from '../components/Loading'
import { Pagination } from '../components/Pagination'
import { formatDate } from '../utils/format'
import { useFetch } from '../utils/useFetch'

const PAGE_SIZE = 20

export function AdminOverduePage() {
  const [page, setPage] = useState(1)

  const loadOverdue = useCallback(() => {
    return fetchOverdueLoanRequests({ page, page_size: PAGE_SIZE })
  }, [page])
  const overdue = useFetch(loadOverdue)

  return (
    <section>
      <h2>期限超過一覧</h2>
      <p>
        返却の受付は<Link to="/admin/requests">申請管理</Link>（状態：貸出中）で行います。
      </p>
      {overdue.status === 'loading' ? <Loading /> : null}
      {overdue.status === 'error' ? (
        <>
          <ErrorMessage message={overdue.error} />
          <button type="button" onClick={overdue.reload}>
            再読み込み
          </button>
        </>
      ) : null}
      {overdue.status === 'success' && overdue.data !== null ? (
        <>
          {overdue.data.items.length === 0 ? (
            <p>期限を過ぎている貸出はありません。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>備品</th>
                  <th>借用者</th>
                  <th>返却予定日</th>
                  <th>超過日数</th>
                  <th>用途</th>
                </tr>
              </thead>
              <tbody>
                {overdue.data.items.map((request) => (
                  <tr key={request.id}>
                    <td>
                      {request.equipment_asset_number} {request.equipment_name}
                    </td>
                    <td>
                      {request.requester_name}（{request.requester_department}）
                    </td>
                    <td>{formatDate(request.due_date)}</td>
                    <td>{request.overdue_days}日</td>
                    <td>{request.purpose}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pagination
            page={overdue.data.page}
            pageSize={overdue.data.page_size}
            total={overdue.data.total}
            onChange={setPage}
          />
        </>
      ) : null}
    </section>
  )
}
