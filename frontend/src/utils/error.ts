/**
 * エラーメッセージの取り出し
 *
 * 【概要】
 * 捕捉した例外から、画面へ表示する日本語メッセージを取り出す。
 */

import { ApiError } from '../api/client'

const FALLBACK_MESSAGE = '予期しないエラーが発生しました'

/** ApiErrorならそのメッセージ、それ以外は既定のメッセージを返す */
export function toErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  return FALLBACK_MESSAGE
}
