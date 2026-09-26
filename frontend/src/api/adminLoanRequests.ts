/**
 * 貸出申請API（管理者向け）
 *
 * 【概要】
 * 申請の一覧取得、承認・却下、管理者による取消、貸出処理、返却処理のAPIを呼び出す。
 */

import type {
  LoanRequestListQuery,
  LoanRequestReasonRequest,
  LoanRequestResponse,
  LoanReturnRequest,
  Page,
} from '../types/api'
import { apiGet, apiPost } from './client'

/** 申請を新しい順に取得する（状態の指定が無い場合は申請中） */
export function fetchAdminLoanRequests(
  query: LoanRequestListQuery,
): Promise<Page<LoanRequestResponse>> {
  return apiGet<Page<LoanRequestResponse>>('/admin/loan-requests', { ...query })
}

/** 申請を承認する（期間が重複する場合はエラー） */
export function approveLoanRequest(loanRequestId: number): Promise<LoanRequestResponse> {
  return apiPost<LoanRequestResponse>(`/admin/loan-requests/${loanRequestId}/approve`)
}

/** 申請を却下する（理由必須） */
export function rejectLoanRequest(
  loanRequestId: number,
  request: LoanRequestReasonRequest,
): Promise<LoanRequestResponse> {
  return apiPost<LoanRequestResponse>(`/admin/loan-requests/${loanRequestId}/reject`, request)
}

/** 承認済みの申請を管理者が取り消す（理由必須） */
export function adminCancelLoanRequest(
  loanRequestId: number,
  request: LoanRequestReasonRequest,
): Promise<LoanRequestResponse> {
  return apiPost<LoanRequestResponse>(`/admin/loan-requests/${loanRequestId}/admin-cancel`, request)
}

/** 貸出処理を行う（承認済みかつ開始日が今日以前のもの） */
export function lendLoanRequest(loanRequestId: number): Promise<LoanRequestResponse> {
  return apiPost<LoanRequestResponse>(`/admin/loan-requests/${loanRequestId}/lend`)
}

/** 返却処理を行う（状態メモは任意） */
export function returnLoanRequest(
  loanRequestId: number,
  request: LoanReturnRequest,
): Promise<LoanRequestResponse> {
  return apiPost<LoanRequestResponse>(`/admin/loan-requests/${loanRequestId}/return`, request)
}
