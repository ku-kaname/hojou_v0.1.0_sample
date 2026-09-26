/**
 * 入力項目（ラベル付き）
 *
 * 【概要】
 * ラベルと入力欄を対応付けて表示する部品。入力欄は子要素として渡し、idでラベルと結び付ける。
 */

import type { ReactNode } from 'react'

interface FormFieldProps {
  id: string
  label: string
  required?: boolean
  hint?: string
  children: ReactNode
}

export function FormField({ id, label, required = false, hint, children }: FormFieldProps) {
  return (
    <div className="form-field">
      <label htmlFor={id}>
        {label}
        {required ? <span className="required-mark">（必須）</span> : null}
      </label>
      {children}
      {hint ? <small className="form-hint">{hint}</small> : null}
    </div>
  )
}
