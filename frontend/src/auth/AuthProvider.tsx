/**
 * 認証状態の提供
 *
 * 【概要】
 * アプリ起動時に保管済みトークンでログイン中ユーザーを確認し、
 * ログイン・ログアウト・トークン差し替えの機能を画面全体へ提供する。
 * APIが401を返した場合は自動的に未ログイン状態へ戻す。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { fetchMe, login } from '../api/auth'
import { setUnauthorizedHandler } from '../api/client'
import type { MeResponse } from '../types/api'
import { AuthContext } from './authContext'
import type { AuthContextValue, AuthStatus } from './authContext'
import { clearToken, getToken, setToken } from './tokenStorage'

interface AuthProviderProps {
  children: ReactNode
}

export function AuthProvider({ children }: AuthProviderProps) {
  const initialStatus: AuthStatus = getToken() === null ? 'unauthenticated' : 'loading'
  const [status, setStatus] = useState<AuthStatus>(initialStatus)
  const [user, setUser] = useState<MeResponse | null>(null)

  const markSignedOut = useCallback(() => {
    clearToken()
    setUser(null)
    setStatus('unauthenticated')
  }, [])

  // 起動時：保管済みトークンが有効か確認する
  useEffect(() => {
    if (getToken() === null) {
      return
    }
    let cancelled = false
    async function restore() {
      try {
        const me = await fetchMe()
        if (!cancelled) {
          setUser(me)
          setStatus('authenticated')
        }
      } catch {
        if (!cancelled) {
          markSignedOut()
        }
      }
    }
    void restore()
    return () => {
      cancelled = true
    }
  }, [markSignedOut])

  // 401受信時に未ログイン状態へ戻す
  useEffect(() => {
    setUnauthorizedHandler(markSignedOut)
    return () => {
      setUnauthorizedHandler(null)
    }
  }, [markSignedOut])

  const applyToken = useCallback(
    async (accessToken: string): Promise<MeResponse> => {
      setToken(accessToken)
      try {
        const me = await fetchMe()
        setUser(me)
        setStatus('authenticated')
        return me
      } catch (error) {
        markSignedOut()
        throw error
      }
    },
    [markSignedOut],
  )

  const signIn = useCallback(
    async (loginId: string, password: string): Promise<MeResponse> => {
      const token = await login({ login_id: loginId, password })
      return applyToken(token.access_token)
    },
    [applyToken],
  )

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, signIn, signOut: markSignedOut, replaceToken: applyToken }),
    [status, user, signIn, markSignedOut, applyToken],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
