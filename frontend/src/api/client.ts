/**
 * API呼び出し共通処理
 *
 * 【概要】
 * バックエンドAPI（/api配下）を呼び出す共通関数を提供する。
 * Authorizationヘッダーの付与、エラーレスポンス（detail）の日本語メッセージ化、
 * 401受信時のトークン破棄と通知、CSVのダウンロード・アップロードを扱う。
 */

import { clearToken, getToken } from '../auth/tokenStorage'
import type { CsvRowError } from '../types/api'

/** クエリパラメーターとして指定できる値 */
export type QueryValue = string | number | boolean | null | undefined

/** クエリパラメーター */
export type QueryParams = Record<string, QueryValue>

/** APIエラー（画面にはmessageをそのまま表示できる） */
export class ApiError extends Error {
  readonly status: number
  readonly rowErrors: CsvRowError[]

  constructor(status: number, message: string, rowErrors: CsvRowError[] = []) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.rowErrors = rowErrors
  }
}

const NETWORK_ERROR_MESSAGE = 'サーバーに接続できません。時間をおいて再度お試しください'
const UNKNOWN_ERROR_MESSAGE = '予期しないエラーが発生しました'
const VALIDATION_ERROR_MESSAGE = '入力内容に誤りがあります'

let unauthorizedHandler: (() => void) | null = null

/** 401（未認証）受信時に呼ばれる処理を登録する（AuthProviderが登録する） */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

/** クエリパラメーターをURL文字列に変換する（未指定・空文字の項目は除外する） */
export function buildQueryString(params?: QueryParams): string {
  if (!params) {
    return ''
  }
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') {
      continue
    }
    const text = String(value)
    search.set(key, text)
  }
  const queryText = search.toString()
  return queryText === '' ? '' : `?${queryText}`
}

/** エラーレスポンスの本文から、画面表示用メッセージと行エラーを取り出す */
async function readError(response: Response): Promise<ApiError> {
  let message = UNKNOWN_ERROR_MESSAGE
  let rowErrors: CsvRowError[] = []
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null) {
      const record = body as { detail?: unknown; errors?: unknown }
      if (typeof record.detail === 'string') {
        message = record.detail
      } else if (Array.isArray(record.detail)) {
        message = VALIDATION_ERROR_MESSAGE
      }
      if (Array.isArray(record.errors)) {
        rowErrors = record.errors as CsvRowError[]
      }
    }
  } catch {
    // 本文がJSONでない場合は既定のメッセージを使う
  }
  return new ApiError(response.status, message, rowErrors)
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  query?: QueryParams
  body?: unknown
  formData?: FormData
  /** ログイン画面など、トークン無しで呼ぶ場合はtrue（401でも自動ログアウトしない） */
  skipAuth?: boolean
}

/** fetchを実行して成功レスポンスを返す。失敗時はApiErrorを投げる */
async function send(path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token && !options.skipAuth) {
    headers['Authorization'] = `Bearer ${token}`
  }
  let body: BodyInit | undefined
  if (options.formData) {
    body = options.formData
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }
  const url = `/api${path}${buildQueryString(options.query)}`

  let response: Response
  try {
    response = await fetch(url, { method: options.method ?? 'GET', headers, body })
  } catch {
    throw new ApiError(0, NETWORK_ERROR_MESSAGE)
  }

  if (!response.ok) {
    const error = await readError(response)
    if (response.status === 401 && !options.skipAuth) {
      clearToken()
      if (unauthorizedHandler) {
        unauthorizedHandler()
      }
    }
    throw error
  }
  return response
}

/** JSONを返すAPIを呼び出す（204の場合はundefinedを返す） */
export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options)
  if (response.status === 204) {
    return undefined as T
  }
  const data = (await response.json()) as T
  return data
}

/** GET */
export function apiGet<T>(path: string, query?: QueryParams): Promise<T> {
  return requestJson<T>(path, { query })
}

/** POST（JSON） */
export function apiPost<T>(path: string, body?: unknown, skipAuth = false): Promise<T> {
  return requestJson<T>(path, { method: 'POST', body, skipAuth })
}

/** PUT（JSON） */
export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, { method: 'PUT', body })
}

/** DELETE */
export function apiDelete<T>(path: string): Promise<T> {
  return requestJson<T>(path, { method: 'DELETE' })
}

/** multipart/form-dataでファイルを送信する（CSVインポート用） */
export function apiUpload<T>(path: string, file: File): Promise<T> {
  const formData = new FormData()
  formData.append('file', file)
  return requestJson<T>(path, { method: 'POST', formData })
}

/** ダウンロード結果 */
export interface DownloadResult {
  blob: Blob
  filename: string
}

/** Content-Dispositionヘッダーからファイル名を取り出す（無ければ既定名） */
function extractFilename(header: string | null, fallback: string): string {
  if (!header) {
    return fallback
  }
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (encoded && encoded[1]) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      return fallback
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header)
  if (plain && plain[1]) {
    return plain[1]
  }
  return fallback
}

/** ファイル（CSV等）をダウンロード用データとして取得する */
export async function apiDownload(
  path: string,
  query: QueryParams | undefined,
  fallbackFilename: string,
): Promise<DownloadResult> {
  const response = await send(path, { query })
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition')
  const filename = extractFilename(disposition, fallbackFilename)
  return { blob, filename }
}

/** 取得したデータをブラウザのファイル保存として実行する */
export function saveBlob(result: DownloadResult): void {
  const url = URL.createObjectURL(result.blob)
  const link = document.createElement('a')
  link.href = url
  link.download = result.filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
