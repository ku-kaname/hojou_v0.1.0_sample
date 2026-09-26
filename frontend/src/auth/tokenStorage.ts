/**
 * トークン保管
 *
 * 【概要】
 * ログイン時に受け取ったアクセストークンをブラウザのsessionStorageに保管する。
 * タブを閉じると自動的に破棄される。トークンをログや画面に出力してはならない。
 */

const TOKEN_KEY = 'hojou_access_token'

/** 保管中のアクセストークンを取得する（未保管・利用不可の場合はnull） */
export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

/** アクセストークンを保管する */
export function setToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token)
  } catch {
    // 保管できない環境では何もしない（次回リクエストで未ログイン扱いになる）
  }
}

/** 保管中のアクセストークンを削除する */
export function clearToken(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // 削除できない環境では何もしない
  }
}
