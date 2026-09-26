/**
 * ユーザー管理画面（S10）
 *
 * 【概要】
 * ユーザーを検索して一覧表示し、登録・編集（無効化を含む）・パスワード初期化を行う。
 * ログインIDは編集できない。初期パスワード・再設定パスワードは画面に保持せず、送信後に破棄する。
 */

import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { createUser, fetchUsers, resetUserPassword, updateUser } from '../api/adminUsers'
import { ErrorMessage } from '../components/ErrorMessage'
import { FormField } from '../components/FormField'
import { Loading } from '../components/Loading'
import { Pagination } from '../components/Pagination'
import type { Role, UserResponse } from '../types/api'
import { toErrorMessage } from '../utils/error'
import { ROLE_LABELS } from '../utils/format'
import { useFetch } from '../utils/useFetch'
import { MIN_PASSWORD_LENGTH } from '../utils/validation'

const PAGE_SIZE = 20
const MAX_PASSWORD_LENGTH = 128
const ROLE_OPTIONS = Object.keys(ROLE_LABELS) as Role[]

/** ユーザーの入力フォーム状態（editingIdがnullの場合は新規登録） */
interface UserFormState {
  editingId: number | null
  loginId: string
  name: string
  department: string
  role: Role
  isActive: boolean
  initialPassword: string
}

const EMPTY_FORM: UserFormState = {
  editingId: null,
  loginId: '',
  name: '',
  department: '',
  role: 'general',
  isActive: true,
  initialPassword: '',
}

/** パスワードを検証する（問題なければnull） */
function validateNewPassword(password: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH || password.length > MAX_PASSWORD_LENGTH) {
    return `パスワードは${MIN_PASSWORD_LENGTH}文字以上${MAX_PASSWORD_LENGTH}文字以内で入力してください`
  }
  return null
}

export function AdminUsersPage() {
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [roleFilter, setRoleFilter] = useState<'' | Role>('')
  const [activeFilter, setActiveFilter] = useState<'' | 'true' | 'false'>('')
  const [page, setPage] = useState(1)
  const [form, setForm] = useState<UserFormState | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [resetTarget, setResetTarget] = useState<UserResponse | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState<string | null>(null)

  const loadUsers = useCallback(() => {
    const trimmedKeyword = keyword.trim()
    return fetchUsers({
      keyword: trimmedKeyword === '' ? undefined : trimmedKeyword,
      role: roleFilter === '' ? undefined : roleFilter,
      is_active: activeFilter === '' ? undefined : activeFilter === 'true',
      page,
      page_size: PAGE_SIZE,
    })
  }, [keyword, roleFilter, activeFilter, page])
  const users = useFetch(loadUsers)

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setKeyword(keywordInput)
    setPage(1)
  }

  function handleRoleFilterChange(value: string) {
    setRoleFilter(value as '' | Role)
    setPage(1)
  }

  function handleActiveFilterChange(value: string) {
    setActiveFilter(value as '' | 'true' | 'false')
    setPage(1)
  }

  function openCreateForm() {
    setForm(EMPTY_FORM)
    setFormError(null)
    setMessage(null)
    setResetTarget(null)
  }

  function openEditForm(user: UserResponse) {
    setForm({
      editingId: user.id,
      loginId: user.login_id,
      name: user.name,
      department: user.department,
      role: user.role,
      isActive: user.is_active,
      initialPassword: '',
    })
    setFormError(null)
    setMessage(null)
    setResetTarget(null)
  }

  function closeForm() {
    setForm(null)
    setFormError(null)
  }

  function updateForm(changes: Partial<UserFormState>) {
    setForm((current) => (current === null ? current : { ...current, ...changes }))
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (form === null) {
      return
    }
    const loginId = form.loginId.trim()
    const name = form.name.trim()
    const department = form.department.trim()
    if (form.editingId === null && (loginId.length < 3 || loginId.length > 32)) {
      setFormError('ユーザーIDは3文字以上32文字以内で入力してください')
      return
    }
    if (name === '') {
      setFormError('氏名を入力してください')
      return
    }
    if (form.editingId === null) {
      const passwordMessage = validateNewPassword(form.initialPassword)
      if (passwordMessage !== null) {
        setFormError(passwordMessage)
        return
      }
    }
    setSubmitting(true)
    setFormError(null)
    try {
      if (form.editingId === null) {
        await createUser({
          login_id: loginId,
          name,
          department,
          role: form.role,
          initial_password: form.initialPassword,
        })
        setMessage('ユーザーを登録しました。初回ログイン時にパスワードの変更が必要です')
      } else {
        await updateUser(form.editingId, {
          name,
          department,
          role: form.role,
          is_active: form.isActive,
        })
        setMessage('ユーザーを更新しました')
      }
      setForm(null)
      users.reload()
    } catch (error) {
      const errorText = toErrorMessage(error)
      setFormError(errorText)
    } finally {
      setSubmitting(false)
    }
  }

  function openResetForm(user: UserResponse) {
    setResetTarget(user)
    setResetPassword('')
    setResetError(null)
    setMessage(null)
    setForm(null)
  }

  function closeResetForm() {
    setResetTarget(null)
    setResetPassword('')
    setResetError(null)
  }

  async function handleReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (resetTarget === null) {
      return
    }
    const passwordMessage = validateNewPassword(resetPassword)
    if (passwordMessage !== null) {
      setResetError(passwordMessage)
      return
    }
    setResetting(true)
    setResetError(null)
    try {
      await resetUserPassword(resetTarget.id, { new_password: resetPassword })
      setMessage(`${resetTarget.name}のパスワードを初期化しました。次回ログイン時に変更が必要です`)
      closeResetForm()
    } catch (error) {
      const errorText = toErrorMessage(error)
      setResetError(errorText)
    } finally {
      setResetting(false)
    }
  }

  return (
    <section>
      <h2>ユーザー管理</h2>

      {message !== null ? (
        <p className="notice" role="status">
          {message}
        </p>
      ) : null}

      <form onSubmit={handleSearch} className="search-form">
        <FormField id="admin-user-keyword" label="キーワード（ユーザーID・氏名）">
          <input
            id="admin-user-keyword"
            type="text"
            value={keywordInput}
            onChange={(event) => setKeywordInput(event.target.value)}
          />
        </FormField>
        <FormField id="admin-user-role" label="権限">
          <select
            id="admin-user-role"
            value={roleFilter}
            onChange={(event) => handleRoleFilterChange(event.target.value)}
          >
            <option value="">すべて</option>
            {ROLE_OPTIONS.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABELS[role]}
              </option>
            ))}
          </select>
        </FormField>
        <FormField id="admin-user-active" label="状態">
          <select
            id="admin-user-active"
            value={activeFilter}
            onChange={(event) => handleActiveFilterChange(event.target.value)}
          >
            <option value="">すべて</option>
            <option value="true">有効</option>
            <option value="false">無効</option>
          </select>
        </FormField>
        <button type="submit">検索</button>
        <button type="button" onClick={openCreateForm}>
          ユーザーを登録
        </button>
      </form>

      {form !== null ? (
        <form onSubmit={handleSubmit} className="card">
          <h3>{form.editingId === null ? 'ユーザーの登録' : 'ユーザーの編集'}</h3>
          <ErrorMessage message={formError} />
          <FormField id="user-login-id" label="ユーザーID" required>
            <input
              id="user-login-id"
              type="text"
              maxLength={32}
              value={form.loginId}
              disabled={form.editingId !== null}
              onChange={(event) => updateForm({ loginId: event.target.value })}
            />
          </FormField>
          <FormField id="user-name" label="氏名" required>
            <input
              id="user-name"
              type="text"
              maxLength={50}
              value={form.name}
              onChange={(event) => updateForm({ name: event.target.value })}
            />
          </FormField>
          <FormField id="user-department" label="部署">
            <input
              id="user-department"
              type="text"
              maxLength={50}
              value={form.department}
              onChange={(event) => updateForm({ department: event.target.value })}
            />
          </FormField>
          <FormField id="user-role" label="権限" required>
            <select
              id="user-role"
              value={form.role}
              onChange={(event) => updateForm({ role: event.target.value as Role })}
            >
              {ROLE_OPTIONS.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
          </FormField>
          {form.editingId === null ? (
            <FormField
              id="user-initial-password"
              label="初期パスワード"
              required
              hint={`${MIN_PASSWORD_LENGTH}文字以上。初回ログイン時に本人が変更します`}
            >
              <input
                id="user-initial-password"
                type="password"
                autoComplete="new-password"
                maxLength={MAX_PASSWORD_LENGTH}
                value={form.initialPassword}
                onChange={(event) => updateForm({ initialPassword: event.target.value })}
              />
            </FormField>
          ) : (
            <FormField id="user-active" label="ログイン">
              <label>
                <input
                  id="user-active"
                  type="checkbox"
                  checked={form.isActive}
                  onChange={(event) => updateForm({ isActive: event.target.checked })}
                />
                有効（チェックを外すと無効化）
              </label>
            </FormField>
          )}
          <div className="button-row">
            <button type="submit" disabled={submitting}>
              {submitting ? '送信中…' : '保存する'}
            </button>
            <button type="button" className="secondary" onClick={closeForm} disabled={submitting}>
              キャンセル
            </button>
          </div>
        </form>
      ) : null}

      {resetTarget !== null ? (
        <form onSubmit={handleReset} className="card">
          <h3>パスワードの初期化（{resetTarget.name}）</h3>
          <ErrorMessage message={resetError} />
          <FormField
            id="user-reset-password"
            label="新しいパスワード"
            required
            hint={`${MIN_PASSWORD_LENGTH}文字以上。次回ログイン時に本人が変更します`}
          >
            <input
              id="user-reset-password"
              type="password"
              autoComplete="new-password"
              maxLength={MAX_PASSWORD_LENGTH}
              value={resetPassword}
              onChange={(event) => setResetPassword(event.target.value)}
            />
          </FormField>
          <div className="button-row">
            <button type="submit" disabled={resetting}>
              {resetting ? '送信中…' : '初期化する'}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={closeResetForm}
              disabled={resetting}
            >
              キャンセル
            </button>
          </div>
        </form>
      ) : null}

      {users.status === 'loading' ? <Loading /> : null}
      {users.status === 'error' ? (
        <>
          <ErrorMessage message={users.error} />
          <button type="button" onClick={users.reload}>
            再読み込み
          </button>
        </>
      ) : null}
      {users.status === 'success' && users.data !== null ? (
        <>
          {users.data.items.length === 0 ? (
            <p>該当するユーザーはいません。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>ユーザーID</th>
                  <th>氏名</th>
                  <th>部署</th>
                  <th>権限</th>
                  <th>状態</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.data.items.map((user) => (
                  <tr key={user.id}>
                    <td>{user.login_id}</td>
                    <td>{user.name}</td>
                    <td>{user.department === '' ? '-' : user.department}</td>
                    <td>{ROLE_LABELS[user.role]}</td>
                    <td>{user.is_active ? '有効' : '無効'}</td>
                    <td>
                      <div className="button-row">
                        <button type="button" onClick={() => openEditForm(user)}>
                          編集
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => openResetForm(user)}
                        >
                          パスワード初期化
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pagination
            page={users.data.page}
            pageSize={users.data.page_size}
            total={users.data.total}
            onChange={setPage}
          />
        </>
      ) : null}
    </section>
  )
}
