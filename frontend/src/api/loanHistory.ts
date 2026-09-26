/**
 * 貸出履歴API
 *
 * 【概要】
 * 貸出履歴の検索とCSV出力のAPIを呼び出す。
 */

import type { LoanHistoryFilter, LoanHistoryQuery, LoanHistoryResponse, Page } from '../types/api'
import { apiDownload, apiGet } from './client'
import type { DownloadResult } from './client'

/** 貸出履歴を検索して一覧取得する */
export function fetchLoanHistory(query: LoanHistoryQuery): Promise<Page<LoanHistoryResponse>> {
  return apiGet<Page<LoanHistoryResponse>>('/admin/loan-history', { ...query })
}

/** 検索条件に一致する貸出履歴をCSVとして取得する（ページングしない） */
export function downloadLoanHistoryCsv(filter: LoanHistoryFilter): Promise<DownloadResult> {
  return apiDownload('/admin/loan-history/export', { ...filter }, 'loan_history.csv')
}
