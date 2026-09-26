/**
 * 理由・メモ入力フォーム
 *
 * 【概要】
 * 却下理由・取消理由・返却時状態メモなど、1つの文章を入力して確定する小さなフォーム。
 * 必須の場合は空白のみの入力を送信させない。送信中は二重送信できない。
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { toErrorMessage } from '../utils/error'
import { ErrorMessage } from './ErrorMessage'
import { FormField } from './FormField'

interface ReasonFormProps {
  id: string
  title: string
  label: string
  required: boolean
  maxLength: number
  submitLabel: string
  onSubmit: (text: string) => Promise<void>
  onCancel: () => void
}

export function ReasonForm({
  id,
  title,
  label,
  required,
  maxLength,
  submitLabel,
  onSubmit,
  onCancel,
}: ReasonFormProps) {
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedText = text.trim()
    if (required && trimmedText === '') {
      setErrorMessage(`${label}を入力してください`)
      return
    }
    setSubmitting(true)
    setErrorMessage(null)
    try {
      await onSubmit(trimmedText)
    } catch (error) {
      const message = toErrorMessage(error)
      setErrorMessage(message)
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card">
      <h3>{title}</h3>
      <ErrorMessage message={errorMessage} />
      <FormField id={id} label={label} required={required} hint={`${maxLength}文字以内`}>
        <textarea
          id={id}
          rows={3}
          maxLength={maxLength}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </FormField>
      <div className="button-row">
        <button type="submit" disabled={submitting}>
          {submitLabel}
        </button>
        <button type="button" className="secondary" onClick={onCancel} disabled={submitting}>
          キャンセル
        </button>
      </div>
    </form>
  )
}
