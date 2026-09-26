/**
 * 単体テスト：API呼び出し共通処理・エラーメッセージの取り出し
 *
 * テスト仕様書：単体テスト仕様書/frontend/共通/入力チェック・整形・API共通（項番26〜38）
 * 設計書：設計書/エンドポイント（エラーレスポンス・認証）
 * テスト対象ファイル：frontend/src/api/client.ts、utils/error.ts
 *
 * 【テストの考え方】
 * サーバーには接続せず、通信（fetch）を偽物（モック）に差し替える。
 * 偽物が返す「サーバーの返事」ごとに、画面へ渡されるエラーや戻り値を確認する。
 */

import { ApiError, apiDownload, apiGet, apiPost, setUnauthorizedHandler } from '../../api/client'
import { clearToken, getToken, setToken } from '../../auth/tokenStorage'
import { toErrorMessage } from '../../utils/error'

/** JSONを本文に持つサーバーの返事を作る */
function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** 偽物のfetchが最後に受け取ったリクエストのヘッダーを取り出す */
function lastRequestHeaders(calls: unknown[][]): Record<string, string> {
  const lastCall = calls[calls.length - 1] ?? []
  const init = lastCall[1] as RequestInit
  return init.headers as Record<string, string>
}

/** 呼び出しが失敗する（ApiErrorを投げる）ことを確認し、そのエラーを返す */
async function catchApiError(action: () => Promise<unknown>): Promise<ApiError> {
  try {
    await action()
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    return error as ApiError
  }
  throw new Error('エラーが発生するはずの呼び出しが成功した')
}

describe('API呼び出し共通処理', () => {
  beforeEach(() => {
    clearToken()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    setUnauthorizedHandler(null)
    clearToken()
  })

  it('項番26：トークンがあれば、Authorizationヘッダーに「Bearer トークン」を付ける', async () => {
    setToken('test-token')
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(jsonResponse({ ok: true })))
    await apiGet('/equipments')
    expect(lastRequestHeaders(fetchMock.mock.calls)['Authorization']).toBe('Bearer test-token')
  })

  it('項番27：トークンが無い場合と、認証不要（ログインなど）の呼び出しでは、ヘッダーを付けない', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(jsonResponse({ ok: true })))
    await apiGet('/equipments')
    expect(lastRequestHeaders(fetchMock.mock.calls)['Authorization']).toBeUndefined()

    setToken('test-token')
    await apiPost('/auth/login', { login_id: 'a' }, true)
    expect(lastRequestHeaders(fetchMock.mock.calls)['Authorization']).toBeUndefined()
  })

  it('項番28：ステータス204（本文なし）はundefinedを返す', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
    await expect(apiGet('/notifications/read')).resolves.toBeUndefined()
  })

  it('項番29：detailが文字列のエラーは、その文字列をメッセージにする', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ detail: '備品が見つかりません' }, 400),
    )
    const error = await catchApiError(() => apiGet('/equipments/1'))
    expect(error.status).toBe(400)
    expect(error.message).toBe('備品が見つかりません')
  })

  it('項番30：入力検証エラー（detailが配列）は「入力内容に誤りがあります」にする', async () => {
    const detail = [{ loc: ['body', 'name'], msg: 'field required', type: 'missing' }]
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ detail }, 422))
    const error = await catchApiError(() => apiPost('/admin/equipments', {}))
    expect(error.status).toBe(422)
    expect(error.message).toBe('入力内容に誤りがあります')
  })

  it('項番31：本文がJSONでないエラーは「予期しないエラーが発生しました」にする', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Internal Server Error', { status: 500 }),
    )
    const error = await catchApiError(() => apiGet('/equipments'))
    expect(error.status).toBe(500)
    expect(error.message).toBe('予期しないエラーが発生しました')
  })

  it('項番32：CSVの行別エラー（errors）はrowErrorsに入る', async () => {
    const errors = [{ row_number: 3, column: '資産番号', message: '資産番号が重複しています' }]
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ detail: 'CSVに誤りがあります', errors }, 400),
    )
    const error = await catchApiError(() => apiPost('/admin/equipments/import'))
    expect(error.message).toBe('CSVに誤りがあります')
    expect(error.rowErrors).toEqual(errors)
  })

  it('項番33：通信に失敗したら、ステータス0の接続エラーになる', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    const error = await catchApiError(() => apiGet('/equipments'))
    expect(error.status).toBe(0)
    expect(error.message).toContain('サーバーに接続できません')
  })

  it('項番34：401を受け取ったら、認証ありの呼び出しではトークンを破棄して通知する。認証不要では何もしない', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(jsonResponse({ detail: '認証が必要です' }, 401)),
    )

    // 認証不要（ログイン失敗など）：トークンは残り、通知もされない
    setToken('test-token')
    await catchApiError(() => apiPost('/auth/login', {}, true))
    expect(getToken()).toBe('test-token')
    expect(handler).not.toHaveBeenCalled()

    // 認証あり：トークンを破棄し、1回だけ通知する
    await catchApiError(() => apiGet('/equipments'))
    expect(getToken()).toBeNull()
    expect(handler).toHaveBeenCalledTimes(1)
  })
})

describe('ダウンロードのファイル名', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  /** Content-Dispositionを指定したCSVの返事で、取り出されるファイル名を調べる */
  async function filenameFor(disposition: string | null): Promise<string> {
    const headers: Record<string, string> = {}
    if (disposition !== null) {
      headers['Content-Disposition'] = disposition
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('a,b', { status: 200, headers }))
    const result = await apiDownload('/admin/loan-history/export', undefined, 'default.csv')
    return result.filename
  }

  it("項番35：filename*=UTF-8'' 指定はURLデコードしたファイル名を返す", async () => {
    const encoded = encodeURIComponent('貸出履歴.csv')
    expect(await filenameFor(`attachment; filename*=UTF-8''${encoded}`)).toBe('貸出履歴.csv')
  })

  it('項番36：filename="名前.csv" 指定はその名前を返す', async () => {
    expect(await filenameFor('attachment; filename="history.csv"')).toBe('history.csv')
  })

  it('項番37：ヘッダーなし・デコードできない値は、指定した既定のファイル名を返す', async () => {
    expect(await filenameFor(null)).toBe('default.csv')
    // 「%E3%」は途中で切れたエンコードで、デコードに失敗する
    expect(await filenameFor("attachment; filename*=UTF-8''%E3%")).toBe('default.csv')
  })
})

describe('toErrorMessage', () => {
  it('項番38：ApiErrorはそのメッセージ、それ以外は既定のメッセージを返す', () => {
    expect(toErrorMessage(new ApiError(400, '備品が見つかりません'))).toBe('備品が見つかりません')
    expect(toErrorMessage(new Error('内部の詳細'))).toBe('予期しないエラーが発生しました')
    expect(toErrorMessage('文字列')).toBe('予期しないエラーが発生しました')
  })
})
