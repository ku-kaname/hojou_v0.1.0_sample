/**
 * エラーメッセージ表示
 *
 * 【概要】
 * 失敗した操作のエラー内容を利用者へ伝える表示部品。メッセージが無い場合は何も表示しない。
 */

interface ErrorMessageProps {
  message: string | null | undefined
}

export function ErrorMessage({ message }: ErrorMessageProps) {
  if (!message) {
    return null
  }
  return (
    <p className="error-message" role="alert">
      {message}
    </p>
  )
}
