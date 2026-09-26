/**
 * 認証状態の利用
 *
 * 【概要】
 * 画面部品から認証状態（ログイン中ユーザー・ログイン・ログアウト）を利用するためのフック。
 * AuthProviderの内側でのみ使用できる。
 */

import { useContext } from 'react'
import { AuthContext } from './authContext'
import type { AuthContextValue } from './authContext'

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (value === null) {
    throw new Error('useAuthはAuthProviderの内側で使用してください')
  }
  return value
}
