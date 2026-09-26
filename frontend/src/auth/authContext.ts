/**
 * 認証状態の共有定義
 *
 * 【概要】
 * ログイン状態とログイン中ユーザー情報を画面全体で共有するためのContextと型を定義する。
 */

import { createContext } from 'react'
import type { MeResponse } from '../types/api'

/** 認証状態（loading：確認中、authenticated：ログイン済み、unauthenticated：未ログイン） */
export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

/** 画面から利用する認証機能 */
export interface AuthContextValue {
  status: AuthStatus
  user: MeResponse | null
  /** ログインする。成功時はログイン中ユーザー情報を返す。失敗時はApiErrorを投げる */
  signIn: (loginId: string, password: string) => Promise<MeResponse>
  /** ログアウトする（トークンを破棄する） */
  signOut: () => void
  /** パスワード変更後に返却された新しいトークンへ切り替え、ユーザー情報を取得し直す */
  replaceToken: (accessToken: string) => Promise<MeResponse>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
