/**
 * 入力チェック
 *
 * 【概要】
 * 画面で入力された内容の、送信前チェック（サーバー側の検証と同じ基準）を行う。
 * 最終的な検証はサーバーが行うため、ここでは利用者へ早く分かりやすく知らせることが目的。
 */

/** 新しいパスワードの最小文字数 */
export const MIN_PASSWORD_LENGTH = 8

/** 入力内容の誤りがあればメッセージを返す（問題なければnull） */
export function validatePasswordInput(
  currentPassword: string,
  newPassword: string,
  confirmPassword: string,
): string | null {
  if (newPassword.length < MIN_PASSWORD_LENGTH) {
    return `新しいパスワードは${MIN_PASSWORD_LENGTH}文字以上で入力してください`
  }
  if (newPassword !== confirmPassword) {
    return '新しいパスワードと確認用の入力が一致しません'
  }
  if (newPassword === currentPassword) {
    return '現在のパスワードと異なるパスワードを入力してください'
  }
  return null
}

/** 用途の最大文字数 */
export const MAX_PURPOSE_LENGTH = 200

/**
 * 貸出申請の入力内容に誤りがあればメッセージを返す（問題なければnull）。
 * 日付は「YYYY-MM-DD」形式の文字列で、todayは今日の日付。
 */
export function validateLoanRequestInput(
  startDate: string,
  dueDate: string,
  purpose: string,
  today: string,
): string | null {
  if (startDate === '' || dueDate === '') {
    return '開始日と返却予定日を入力してください'
  }
  if (startDate < today) {
    return '開始日は今日以降の日付を指定してください'
  }
  if (dueDate < startDate) {
    return '返却予定日は開始日以降の日付を指定してください'
  }
  const trimmedPurpose = purpose.trim()
  if (trimmedPurpose === '') {
    return '用途を入力してください'
  }
  if (trimmedPurpose.length > MAX_PURPOSE_LENGTH) {
    return `用途は${MAX_PURPOSE_LENGTH}文字以内で入力してください`
  }
  return null
}
