/**
 * 貸出申請API（一般ユーザー向け）
 *
 * 【概要】
 * 貸出申請の作成、自分の申請の一覧取得、申請の取消のAPIを呼び出す。
 */

import type {
  LoanRequestCreateRequest,
  LoanRequestListQuery,
  LoanRequestResponse,
  Page,
} from '../types/api'
import { apiGet, apiPost } from './client'

/** 貸出を申請する（申請者はログイン中のユーザー） */
export function createLoanRequest(request: LoanRequestCreateRequest): Promise<LoanRequestResponse> {
  return apiPost<LoanRequestResponse>('/loan-requests', request)
}

/** 自分の申請を新しい順に取得する */
export function fetchMyLoanRequests(
  query: LoanRequestListQuery,
): Promise<Page<LoanRequestResponse>> {
  return apiGet<Page<LoanRequestResponse>>('/loan-requests/me', { ...query })
}

/** 自分の申請（申請中・承認済み）を取り消す */
export function cancelLoanRequest(loanRequestId: number): Promise<LoanRequestResponse> {
  return apiPost<LoanRequestResponse>(`/loan-requests/${loanRequestId}/cancel`)
}

/** 期限超過の貸出（管理者のみ）を取得する。ホームの件数表示に使う */
export function fetchOverdueLoanRequests(
  query: LoanRequestListQuery,
): Promise<Page<LoanRequestResponse>> {
  return apiGet<Page<LoanRequestResponse>>('/admin/loan-requests/overdue', { ...query })
}
