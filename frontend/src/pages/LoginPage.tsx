/**
 * ログイン画面（S01）
 *
 * 【概要】
 * ユーザーID・パスワードで認証する。成功したらホーム画面（または遷移元の画面）へ移動する。
 * 初期パスワードの変更が必要なユーザーはパスワード変更画面へ移動する。
 * エラー内容（誤り・アカウントロック等）はサーバーの日本語メッセージをそのまま表示する。
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { ErrorMessage } from '../components/ErrorMessage'
import { FormField } from '../components/FormField'
import { toErrorMessage } from '../utils/error'

interface LoginLocationState {
  from?: string
}

export function LoginPage() {
  const { status, user, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [loginId, setLoginId] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const locationState = location.state as LoginLocationState | null
  const destination = locationState?.from ?? '/'

  // ログイン済みの場合はログイン画面を表示せず移動する（ログイン処理直後の移動もここで行う）
  if (status === 'authenticated' && user !== null && !submitting) {
    const next = user.must_change_password ? '/password-change' : destination
    return <Navigate to={next} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const me = await signIn(loginId, password)
      const next = me.must_change_password ? '/password-change' : destination
      navigate(next, { replace: true })
    } catch (error) {
      setErrorMessage(toErrorMessage(error))
      setSubmitting(false)
    }
  }

  return (
    <main className="login-page">
      <h1>社内備品貸出管理システム</h1>
      <h2>ログイン</h2>
      <form onSubmit={handleSubmit} className="card">
        <ErrorMessage message={errorMessage} />
        <FormField id="login-id" label="ユーザーID" required>
          <input
            id="login-id"
            type="text"
            autoComplete="username"
            value={loginId}
            onChange={(event) => setLoginId(event.target.value)}
            required
          />
        </FormField>
        <FormField id="login-password" label="パスワード" required>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </FormField>
        <button type="submit" disabled={submitting}>
          {submitting ? 'ログイン中…' : 'ログイン'}
        </button>
      </form>
    </main>
  )
}
