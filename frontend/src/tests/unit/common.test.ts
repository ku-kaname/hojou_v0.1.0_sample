/**
 * 単体テスト：整形・ページ数計算・クエリ文字列・通知の移動先
 *
 * テスト仕様書：単体テスト仕様書/frontend/共通/入力チェック・整形・API共通（項番15〜25）
 * 設計書：要件定義書/画面一覧（S02・S05・S07〜S11）、設計書/エンドポイント
 * テスト対象ファイル：frontend/src/utils/format.ts、utils/pagination.ts、api/client.ts（buildQueryString）
 *
 * 【テストの考え方】
 * 画面を使わず、値を関数へ渡して返り値を確認する。
 */

import { buildQueryString } from '../../api/client'
import type { LoanStatus, NotificationType } from '../../types/api'
import {
  formatDate,
  formatDateTime,
  isCancelable,
  notificationLink,
  toDateInputValue,
} from '../../utils/format'
import { calcTotalPages } from '../../utils/pagination'

describe('formatDate / formatDateTime', () => {
  it('項番15：日付をスラッシュ区切りにする', () => {
    expect(formatDate('2026-09-26')).toBe('2026/09/26')
  })

  it('項番16：未指定・空文字は「-」にする', () => {
    expect(formatDate(null)).toBe('-')
    expect(formatDate(undefined)).toBe('-')
    expect(formatDate('')).toBe('-')
  })

  it('項番17：日時を分まで表示する', () => {
    expect(formatDateTime('2026-09-26T06:00:00+09:00')).toBe('2026/09/26 06:00')
  })

  it('項番18：時刻がない場合は日付のみ、未指定は「-」にする', () => {
    expect(formatDateTime('2026-09-26')).toBe('2026/09/26')
    expect(formatDateTime(null)).toBe('-')
  })
})

describe('toDateInputValue', () => {
  it('項番19：月・日を2桁にそろえる（1月5日 → 01-05）', () => {
    // 月は0始まりのため、0が1月を表す
    expect(toDateInputValue(new Date(2026, 0, 5))).toBe('2026-01-05')
  })
})

describe('calcTotalPages', () => {
  it('項番20：総ページ数はページサイズで切り上げ、最小1', () => {
    const cases: [number, number][] = [
      [0, 1],
      [20, 1],
      [21, 2],
      [40, 2],
      [41, 3],
    ]
    for (const [total, pages] of cases) {
      expect(calcTotalPages(total, 20)).toBe(pages)
    }
  })

  it('項番21：ページサイズが0以下なら1', () => {
    expect(calcTotalPages(100, 0)).toBe(1)
  })
})

describe('isCancelable', () => {
  it('項番22：取消できるのは承認待ち・承認済みだけ', () => {
    const cancelable: LoanStatus[] = ['requested', 'approved']
    const notCancelable: LoanStatus[] = ['lent', 'returned', 'rejected', 'canceled']
    for (const status of cancelable) {
      expect(isCancelable(status)).toBe(true)
    }
    for (const status of notCancelable) {
      expect(isCancelable(status)).toBe(false)
    }
  })
})

describe('notificationLink', () => {
  it('項番23：通知の種類と権限ごとに移動先が変わる', () => {
    expect(notificationLink('new_request', true)).toBe('/admin/requests')
    expect(notificationLink('overdue', true)).toBe('/admin/overdue')
    // 期限超過でも一般ユーザーは管理者画面へ行かない
    expect(notificationLink('overdue', false)).toBe('/my-requests')
    const others: NotificationType[] = ['approved', 'rejected', 'canceled', 'due_soon']
    for (const type of others) {
      expect(notificationLink(type, false)).toBe('/my-requests')
    }
  })
})

describe('buildQueryString', () => {
  it('項番24：文字列・数値のほか、0・falseも除外せずに変換する', () => {
    const result = buildQueryString({ keyword: 'PC', page: 2, offset: 0, flag: false })
    expect(result).toBe('?keyword=PC&page=2&offset=0&flag=false')
  })

  it('項番25：未指定・空文字・nullの項目は除外し、指定が無ければ空文字を返す', () => {
    const result = buildQueryString({ keyword: '', page: 2, status: undefined, owner: null })
    expect(result).toBe('?page=2')
    expect(buildQueryString({ keyword: '' })).toBe('')
    expect(buildQueryString()).toBe('')
  })
})
