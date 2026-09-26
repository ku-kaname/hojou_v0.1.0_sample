/**
 * パスワード変更画面（S06）
 *
 * 【概要】
 * 現在のパスワードと新しいパスワードを入力し、自分のパスワードを変更する。
 * 初回ログイン時（初期パスワード変更が必要な場合）もこの画面で変更する。
 * 変更に成功すると新しいトークンへ切り替わり（旧トークンは失効する）、ホーム画面へ移動する。
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { changePassword } from '../api/auth'
import { useAuth } from '../auth/useAuth'
import { ErrorMessage } from '../components/ErrorMessage'
import { FormField } from '../components/FormField'
import { toErrorMessage } from '../utils/error'
import { MIN_PASSWORD_LENGTH, validatePasswordInput } from '../utils/validation'

export function PasswordChangePage() {
  const { user, replaceToken } = useAuth()
  const navigate = useNavigate()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const validationMessage = validatePasswordInput(currentPassword, newPassword, confirmPassword)
    if (validationMessage !== null) {
      setErrorMessage(validationMessage)
      return
    }
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const token = await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      await replaceToken(token.access_token)
      navigate('/', { replace: true })
    } catch (error) {
      setErrorMessage(toErrorMessage(error))
      setSubmitting(false)
    }
  }

  return (
    <section>
      <h2>パスワード変更</h2>
      {user?.must_change_password ? (
        <p className="notice">初期パスワードのままです。新しいパスワードへ変更してください。</p>
      ) : null}
      <form onSubmit={handleSubmit} className="card">
        <ErrorMessage message={errorMessage} />
        <FormField id="current-password" label="現在のパスワード" required>
          <input
            id="current-password"
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            required
          />
        </FormField>
        <FormField
          id="new-password"
          label="新しいパスワード"
          required
          hint={`${MIN_PASSWORD_LENGTH}文字以上で入力してください`}
        >
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            required
          />
        </FormField>
        <FormField id="confirm-password" label="新しいパスワード（確認）" required>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            required
          />
        </FormField>
        <button type="submit" disabled={submitting}>
          {submitting ? '変更中…' : '変更する'}
        </button>
      </form>
    </section>
  )
}
