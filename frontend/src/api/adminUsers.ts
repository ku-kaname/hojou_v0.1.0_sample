/**
 * ユーザー管理API
 *
 * 【概要】
 * ユーザーの一覧取得・登録・編集（無効化を含む）・パスワード初期化のAPIを呼び出す。
 */

import type {
  Page,
  PasswordResetRequest,
  UserCreateRequest,
  UserListQuery,
  UserResponse,
  UserUpdateRequest,
} from '../types/api'
import { apiGet, apiPost, apiPut } from './client'

/** ユーザーを検索して一覧取得する */
export function fetchUsers(query: UserListQuery): Promise<Page<UserResponse>> {
  return apiGet<Page<UserResponse>>('/admin/users', { ...query })
}

/** ユーザーを登録する（初期パスワードでの初回ログイン時に変更が必要になる） */
export function createUser(request: UserCreateRequest): Promise<UserResponse> {
  return apiPost<UserResponse>('/admin/users', request)
}

/** ユーザーを編集する（有効フラグによる無効化・再有効化を含む） */
export function updateUser(userId: number, request: UserUpdateRequest): Promise<UserResponse> {
  return apiPut<UserResponse>(`/admin/users/${userId}`, request)
}

/** ユーザーのパスワードを初期化する（次回ログイン時に変更が必要になる） */
export function resetUserPassword(userId: number, request: PasswordResetRequest): Promise<void> {
  return apiPost<void>(`/admin/users/${userId}/reset-password`, request)
}
