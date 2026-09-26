import { buildQueryString } from '../../api/client'
import { formatDate, formatDateTime } from '../../utils/format'
import { calcTotalPages } from '../../utils/pagination'

describe('buildQueryString', () => {
  it('未指定・空文字の項目を除外する', () => {
    const result = buildQueryString({ keyword: '', page: 2, status: undefined, flag: false })
    expect(result).toBe('?page=2&flag=false')
  })

  it('指定が無ければ空文字を返す', () => {
    expect(buildQueryString()).toBe('')
  })
})

describe('formatDate / formatDateTime', () => {
  it('日付をスラッシュ区切りにする', () => {
    expect(formatDate('2026-09-26')).toBe('2026/09/26')
    expect(formatDate(null)).toBe('-')
  })

  it('日時を分まで表示する', () => {
    expect(formatDateTime('2026-09-26T06:00:00+09:00')).toBe('2026/09/26 06:00')
  })
})

describe('calcTotalPages', () => {
  it('総ページ数は最小1', () => {
    expect(calcTotalPages(0, 20)).toBe(1)
    expect(calcTotalPages(41, 20)).toBe(3)
  })
})
