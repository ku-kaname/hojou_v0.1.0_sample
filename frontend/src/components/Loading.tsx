/**
 * 読み込み中表示
 *
 * 【概要】
 * データ取得中であることを利用者へ伝える表示部品。
 */

interface LoadingProps {
  message?: string
}

export function Loading({ message = '読み込み中です…' }: LoadingProps) {
  return (
    <p className="loading" role="status" aria-live="polite">
      {message}
    </p>
  )
}
