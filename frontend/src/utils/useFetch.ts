/**
 * データ取得フック
 *
 * 【概要】
 * 画面表示時にデータを取得し、「読み込み中」「成功」「失敗」の状態を返す。
 * loaderが変わる（検索条件が変わる）か、reloadが呼ばれると取得し直す。
 * loaderはuseCallbackで作成すること（毎回作り直すと取得を繰り返す）。
 */

import { useCallback, useEffect, useState } from 'react'
import { toErrorMessage } from './error'

/** 取得状態（loading：読み込み中、success：成功、error：失敗） */
export type FetchStatus = 'loading' | 'success' | 'error'

export interface FetchResult<T> {
  status: FetchStatus
  data: T | null
  error: string | null
  /** 同じ条件で取得し直す */
  reload: () => void
}

interface Settled<T> {
  loader: () => Promise<T>
  token: number
  data: T | null
  error: string | null
}

export function useFetch<T>(loader: () => Promise<T>): FetchResult<T> {
  const [token, setToken] = useState(0)
  const [settled, setSettled] = useState<Settled<T> | null>(null)

  useEffect(() => {
    let cancelled = false
    async function run() {
      try {
        const data = await loader()
        if (!cancelled) {
          setSettled({ loader, token, data, error: null })
        }
      } catch (error) {
        if (!cancelled) {
          const message = toErrorMessage(error)
          setSettled({ loader, token, data: null, error: message })
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [loader, token])

  const reload = useCallback(() => {
    setToken((current) => current + 1)
  }, [])

  const isCurrent = settled !== null && settled.loader === loader && settled.token === token
  if (!isCurrent) {
    return { status: 'loading', data: null, error: null, reload }
  }
  if (settled.error !== null) {
    return { status: 'error', data: null, error: settled.error, reload }
  }
  return { status: 'success', data: settled.data, error: null, reload }
}
