/**
 * 認証API
 *
 * 【概要】
 * ログイン・自分の情報取得・パスワード変更のAPIを呼び出す。
 */

import type { LoginRequest, MeResponse, PasswordChangeRequest, TokenResponse } from '../types/api'
import { apiGet, apiPost, apiPut } from './client'

/** ログインしてアクセストークンを取得する（トークン無しで呼ぶ） */
export function login(request: LoginRequest): Promise<TokenResponse> {
  return apiPost<TokenResponse>('/auth/login', request, true)
}

/** ログイン中のユーザー情報を取得する */
export function fetchMe(): Promise<MeResponse> {
  return apiGet<MeResponse>('/users/me')
}

/** ログイン中のユーザーのパスワードを変更する（新しいトークンが返る。旧トークンは失効する） */
export function changePassword(request: PasswordChangeRequest): Promise<TokenResponse> {
  return apiPut<TokenResponse>('/users/me/password', request)
}
