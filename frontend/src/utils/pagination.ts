/**
 * ページ数の計算
 *
 * 【概要】
 * 一覧のページ送りで使う総ページ数を求める。
 */

/** 総件数とページサイズから総ページ数を求める（最小1） */
export function calcTotalPages(total: number, pageSize: number): number {
  if (pageSize <= 0) {
    return 1
  }
  const pages = Math.ceil(total / pageSize)
  return Math.max(pages, 1)
}
