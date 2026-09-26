/**
 * ページ送り
 *
 * 【概要】
 * 一覧のページ移動（前へ・次へ）と件数表示を行う部品。総ページ数が1以下でも件数は表示する。
 */

import { calcTotalPages } from '../utils/pagination'

interface PaginationProps {
  page: number
  pageSize: number
  total: number
  onChange: (page: number) => void
}

export function Pagination({ page, pageSize, total, onChange }: PaginationProps) {
  const totalPages = calcTotalPages(total, pageSize)

  function handlePrev() {
    onChange(page - 1)
  }

  function handleNext() {
    onChange(page + 1)
  }

  return (
    <nav className="pagination" aria-label="ページ送り">
      <span>全{total}件</span>
      <button type="button" onClick={handlePrev} disabled={page <= 1}>
        前へ
      </button>
      <span>
        {page} / {totalPages} ページ
      </span>
      <button type="button" onClick={handleNext} disabled={page >= totalPages}>
        次へ
      </button>
    </nav>
  )
}
