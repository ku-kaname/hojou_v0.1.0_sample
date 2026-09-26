/**
 * 状態バッジ
 *
 * 【概要】
 * 申請状態を色付きのラベルで表示する部品。期限超過の場合は警告表示を追加する。
 */

import type { LoanStatus } from '../types/api'
import { loanStatusLabel } from '../utils/format'

interface StatusBadgeProps {
  status: LoanStatus
  overdue?: boolean
}

export function StatusBadge({ status, overdue = false }: StatusBadgeProps) {
  return (
    <span className="status-badges">
      <span className={`badge badge-${status}`}>{loanStatusLabel(status)}</span>
      {overdue ? <span className="badge badge-overdue">期限超過</span> : null}
    </span>
  )
}
