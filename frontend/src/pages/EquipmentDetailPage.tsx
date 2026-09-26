/**
 * 備品詳細・貸出申請画面（S04）
 *
 * 【概要】
 * 備品の情報と予約状況（承認済み・貸出中の期間一覧）を表示し、貸出を申請する。
 * 申請が成功すると、申請内容と「自分の申請一覧」への案内を表示する。
 * 期間の重複などのエラーはサーバーの日本語メッセージをそのまま表示する。
 */

import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEquipment, fetchReservations } from '../api/equipments'
import { createLoanRequest } from '../api/loanRequests'
import { ErrorMessage } from '../components/ErrorMessage'
import { FormField } from '../components/FormField'
import { Loading } from '../components/Loading'
import type { LoanRequestResponse } from '../types/api'
import { toErrorMessage } from '../utils/error'
import { AVAILABILITY_LABELS, formatDate, toDateInputValue } from '../utils/format'
import { useFetch } from '../utils/useFetch'
import { MAX_PURPOSE_LENGTH, validateLoanRequestInput } from '../utils/validation'

/** URLの備品IDを数値にする（不正な場合はnull） */
function parseEquipmentId(value: string | undefined): number | null {
  if (value === undefined || !/^\d+$/.test(value)) {
    return null
  }
  return Number(value)
}

export function EquipmentDetailPage() {
  const params = useParams()
  const equipmentId = parseEquipmentId(params.equipmentId)

  const loadEquipment = useCallback(() => {
    if (equipmentId === null) {
      return Promise.reject(new Error('備品IDが不正です'))
    }
    return fetchEquipment(equipmentId)
  }, [equipmentId])
  const equipment = useFetch(loadEquipment)

  const loadReservations = useCallback(() => {
    if (equipmentId === null) {
      return Promise.reject(new Error('備品IDが不正です'))
    }
    return fetchReservations(equipmentId)
  }, [equipmentId])
  const reservations = useFetch(loadReservations)

  const today = toDateInputValue(new Date())
  const [startDate, setStartDate] = useState(today)
  const [dueDate, setDueDate] = useState(today)
  const [purpose, setPurpose] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [created, setCreated] = useState<LoanRequestResponse | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (equipmentId === null) {
      return
    }
    const validationMessage = validateLoanRequestInput(startDate, dueDate, purpose, today)
    if (validationMessage !== null) {
      setErrorMessage(validationMessage)
      return
    }
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const response = await createLoanRequest({
        equipment_id: equipmentId,
        start_date: startDate,
        due_date: dueDate,
        purpose: purpose.trim(),
      })
      setCreated(response)
      setPurpose('')
      reservations.reload()
      equipment.reload()
    } catch (error) {
      setErrorMessage(toErrorMessage(error))
    } finally {
      setSubmitting(false)
    }
  }

  if (equipmentId === null) {
    return (
      <section>
        <ErrorMessage message="備品の指定が正しくありません" />
        <Link to="/equipments">備品一覧へ戻る</Link>
      </section>
    )
  }
  if (equipment.status === 'loading') {
    return <Loading />
  }
  if (equipment.status === 'error' || equipment.data === null) {
    return (
      <section>
        <ErrorMessage message={equipment.error ?? '備品を取得できませんでした'} />
        <Link to="/equipments">備品一覧へ戻る</Link>
      </section>
    )
  }

  const item = equipment.data
  const reservationItems = reservations.data?.items ?? []

  return (
    <section>
      <h2>備品詳細・貸出申請</h2>
      <p>
        <Link to="/equipments">← 備品一覧へ戻る</Link>
      </p>

      <table className="detail-table">
        <tbody>
          <tr>
            <th>資産番号</th>
            <td>{item.asset_number}</td>
          </tr>
          <tr>
            <th>備品名</th>
            <td>{item.name}</td>
          </tr>
          <tr>
            <th>分類</th>
            <td>{item.category}</td>
          </tr>
          <tr>
            <th>説明</th>
            <td>{item.description === '' ? '-' : item.description}</td>
          </tr>
          <tr>
            <th>保管場所</th>
            <td>{item.location === '' ? '-' : item.location}</td>
          </tr>
          <tr>
            <th>貸出状況</th>
            <td>
              {AVAILABILITY_LABELS[item.availability]}
              {item.availability === 'lent'
                ? `（返却予定 ${formatDate(item.current_due_date)}${item.is_overdue ? '・期限超過' : ''}）`
                : ''}
            </td>
          </tr>
        </tbody>
      </table>

      <h3>予約状況</h3>
      {reservations.status === 'loading' ? <Loading /> : null}
      {reservations.status === 'error' ? (
        <>
          <ErrorMessage message={reservations.error} />
          <button type="button" onClick={reservations.reload}>
            再読み込み
          </button>
        </>
      ) : null}
      {reservations.status === 'success' ? (
        reservationItems.length === 0 ? (
          <p>現在、予約はありません。</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>開始日</th>
                <th>返却予定日</th>
                <th>占有終了日</th>
                <th>状態</th>
                {reservationItems.some((reservation) => reservation.borrower_name !== null) ? (
                  <th>借用者</th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {reservationItems.map((reservation) => (
                <tr key={`${reservation.start_date}-${reservation.due_date}`}>
                  <td>{formatDate(reservation.start_date)}</td>
                  <td>{formatDate(reservation.due_date)}</td>
                  <td>{formatDate(reservation.occupied_until)}</td>
                  <td>{reservation.status === 'lent' ? '貸出中' : '承認済み（貸出前）'}</td>
                  {reservation.borrower_name !== null ? <td>{reservation.borrower_name}</td> : null}
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : null}

      <h3>貸出申請</h3>
      {created !== null ? (
        <p className="notice" role="status">
          申請しました（{formatDate(created.start_date)}〜{formatDate(created.due_date)}）。
          承認結果は通知でお知らせします。
          <Link to="/my-requests">自分の申請一覧を見る</Link>
        </p>
      ) : null}
      {item.is_active ? (
        <form onSubmit={handleSubmit} className="card">
          <ErrorMessage message={errorMessage} />
          <FormField id="loan-start-date" label="開始日" required>
            <input
              id="loan-start-date"
              type="date"
              min={today}
              value={startDate}
              onChange={(event) => setStartDate(event.target.value)}
              required
            />
          </FormField>
          <FormField id="loan-due-date" label="返却予定日" required>
            <input
              id="loan-due-date"
              type="date"
              min={startDate}
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
              required
            />
          </FormField>
          <FormField
            id="loan-purpose"
            label="用途"
            required
            hint={`${MAX_PURPOSE_LENGTH}文字以内で入力してください`}
          >
            <textarea
              id="loan-purpose"
              rows={3}
              maxLength={MAX_PURPOSE_LENGTH}
              value={purpose}
              onChange={(event) => setPurpose(event.target.value)}
              required
            />
          </FormField>
          <button type="submit" disabled={submitting}>
            {submitting ? '申請中…' : '申請する'}
          </button>
        </form>
      ) : (
        <p>この備品は現在、貸出を受け付けていません。</p>
      )}
    </section>
  )
}
