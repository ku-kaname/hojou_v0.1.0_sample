/**
 * 申請管理画面（S07）
 *
 * 【概要】
 * 貸出申請を状態で絞り込んで一覧表示し、状態に応じた操作を行う。
 *   申請中：承認／却下（理由必須）
 *   承認済み：貸出処理／取消（理由必須）
 *   貸出中：返却処理（状態メモは任意）
 * 承認・貸出処理は確認の上で実行し、却下・取消・返却は理由（メモ）の入力フォームで確定する。
 */

import { useCallback, useState } from 'react'
import {
  adminCancelLoanRequest,
  approveLoanRequest,
  fetchAdminLoanRequests,
  lendLoanRequest,
  rejectLoanRequest,
  returnLoanRequest,
} from '../api/adminLoanRequests'
import { ErrorMessage } from '../components/ErrorMessage'
import { Loading } from '../components/Loading'
import { Pagination } from '../components/Pagination'
import { ReasonForm } from '../components/ReasonForm'
import { StatusBadge } from '../components/StatusBadge'
import type { LoanRequestResponse, LoanStatus } from '../types/api'
import { toErrorMessage } from '../utils/error'
import { LOAN_STATUS_LABELS, formatDate, formatDateTime } from '../utils/format'
import { useFetch } from '../utils/useFetch'

const PAGE_SIZE = 20
const MAX_REASON_LENGTH = 200
const STATUS_OPTIONS = Object.keys(LOAN_STATUS_LABELS) as LoanStatus[]

/** 理由（メモ）の入力が必要な操作 */
type ReasonActionKind = 'reject' | 'admin-cancel' | 'return'

interface ReasonAction {
  kind: ReasonActionKind
  request: LoanRequestResponse
}

/** 理由入力フォームの表示内容 */
const REASON_FORM_TEXTS: Record<
  ReasonActionKind,
  { title: string; label: string; required: boolean; submitLabel: string }
> = {
  reject: { title: '申請の却下', label: '却下理由', required: true, submitLabel: '却下する' },
  'admin-cancel': {
    title: '承認済み申請の取消',
    label: '取消理由',
    required: true,
    submitLabel: '取り消す',
  },
  return: {
    title: '返却処理',
    label: '返却時の状態メモ',
    required: false,
    submitLabel: '返却する',
  },
}

export function AdminRequestsPage() {
  const [status, setStatus] = useState<LoanStatus>('requested')
  const [page, setPage] = useState(1)
  const [reasonAction, setReasonAction] = useState<ReasonAction | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const loadRequests = useCallback(() => {
    return fetchAdminLoanRequests({ status, page, page_size: PAGE_SIZE })
  }, [status, page])
  const requests = useFetch(loadRequests)

  function handleStatusChange(value: string) {
    setStatus(value as LoanStatus)
    setPage(1)
    setReasonAction(null)
    setMessage(null)
    setActionError(null)
  }

  /** 確認のみで実行できる操作（承認・貸出処理）を行う */
  async function runSimpleAction(
    request: LoanRequestResponse,
    confirmText: string,
    action: (loanRequestId: number) => Promise<LoanRequestResponse>,
    doneText: string,
  ) {
    const confirmed = window.confirm(confirmText)
    if (!confirmed) {
      return
    }
    setBusyId(request.id)
    setActionError(null)
    setMessage(null)
    try {
      await action(request.id)
      setMessage(doneText)
      requests.reload()
    } catch (error) {
      const errorText = toErrorMessage(error)
      setActionError(errorText)
    } finally {
      setBusyId(null)
    }
  }

  function handleApprove(request: LoanRequestResponse) {
    const confirmText = `「${request.equipment_name}」（${request.requester_name}）の申請を承認します。よろしいですか？`
    return runSimpleAction(request, confirmText, approveLoanRequest, '申請を承認しました')
  }

  function handleLend(request: LoanRequestResponse) {
    const confirmText = `「${request.equipment_name}」を${request.requester_name}へ貸し出します。よろしいですか？`
    return runSimpleAction(request, confirmText, lendLoanRequest, '貸出処理を行いました')
  }

  /** 理由（メモ）入力フォームの確定時に呼ばれる */
  async function submitReasonAction(text: string) {
    if (reasonAction === null) {
      return
    }
    const target = reasonAction.request
    if (reasonAction.kind === 'reject') {
      await rejectLoanRequest(target.id, { reason: text })
      setMessage('申請を却下しました')
    } else if (reasonAction.kind === 'admin-cancel') {
      await adminCancelLoanRequest(target.id, { reason: text })
      setMessage('申請を取り消しました')
    } else {
      await returnLoanRequest(target.id, { return_note: text })
      setMessage('返却処理を行いました')
    }
    setActionError(null)
    setReasonAction(null)
    requests.reload()
  }

  function closeReasonAction() {
    setReasonAction(null)
  }

  const formTexts = reasonAction === null ? null : REASON_FORM_TEXTS[reasonAction.kind]

  return (
    <section>
      <h2>申請管理</h2>
      <div className="form-field">
        <label htmlFor="admin-filter-status">状態</label>
        <select
          id="admin-filter-status"
          value={status}
          onChange={(event) => handleStatusChange(event.target.value)}
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {LOAN_STATUS_LABELS[option]}
            </option>
          ))}
        </select>
      </div>

      {message !== null ? (
        <p className="notice" role="status">
          {message}
        </p>
      ) : null}
      <ErrorMessage message={actionError} />

      {reasonAction !== null && formTexts !== null ? (
        <ReasonForm
          key={`${reasonAction.kind}-${reasonAction.request.id}`}
          id="admin-reason"
          title={`${formTexts.title}（${reasonAction.request.equipment_name}・${reasonAction.request.requester_name}）`}
          label={formTexts.label}
          required={formTexts.required}
          maxLength={MAX_REASON_LENGTH}
          submitLabel={formTexts.submitLabel}
          onSubmit={submitReasonAction}
          onCancel={closeReasonAction}
        />
      ) : null}

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
            <p>該当する申請はありません。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>備品</th>
                  <th>申請者</th>
                  <th>期間</th>
                  <th>用途</th>
                  <th>状態</th>
                  <th>理由・メモ</th>
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
                      {request.requester_name}（{request.requester_department}）
                    </td>
                    <td>
                      {formatDate(request.start_date)}〜{formatDate(request.due_date)}
                    </td>
                    <td>{request.purpose}</td>
                    <td>
                      <StatusBadge status={request.status} overdue={request.is_overdue} />
                    </td>
                    <td>{request.reason === '' ? request.return_note : request.reason}</td>
                    <td>{formatDateTime(request.requested_at)}</td>
                    <td>
                      <div className="button-row">
                        {request.status === 'requested' ? (
                          <>
                            <button
                              type="button"
                              disabled={busyId === request.id}
                              onClick={() => handleApprove(request)}
                            >
                              承認
                            </button>
                            <button
                              type="button"
                              className="danger"
                              onClick={() => setReasonAction({ kind: 'reject', request })}
                            >
                              却下
                            </button>
                          </>
                        ) : null}
                        {request.status === 'approved' ? (
                          <>
                            <button
                              type="button"
                              disabled={busyId === request.id}
                              onClick={() => handleLend(request)}
                            >
                              貸出処理
                            </button>
                            <button
                              type="button"
                              className="danger"
                              onClick={() => setReasonAction({ kind: 'admin-cancel', request })}
                            >
                              取消
                            </button>
                          </>
                        ) : null}
                        {request.status === 'lent' ? (
                          <button
                            type="button"
                            onClick={() => setReasonAction({ kind: 'return', request })}
                          >
                            返却処理
                          </button>
                        ) : null}
                      </div>
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
