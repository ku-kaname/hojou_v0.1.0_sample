/**
 * 備品一覧画面（S03）
 *
 * 【概要】
 * 備品をキーワード・分類・貸出状況で検索し、一覧表示する。
 * 各備品の貸出状況（貸出可／貸出中・返却予定日・期限超過）を確認し、詳細画面へ進める。
 */

import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { fetchCategories, fetchEquipments } from '../api/equipments'
import { ErrorMessage } from '../components/ErrorMessage'
import { Loading } from '../components/Loading'
import { Pagination } from '../components/Pagination'
import type { Availability, EquipmentListQuery, EquipmentResponse } from '../types/api'
import { AVAILABILITY_LABELS, formatDate } from '../utils/format'
import { useFetch } from '../utils/useFetch'

const PAGE_SIZE = 20

/** 検索フォームの入力値（空文字は「指定なし」） */
interface SearchForm {
  keyword: string
  category: string
  availability: '' | Availability
}

const EMPTY_FORM: SearchForm = { keyword: '', category: '', availability: '' }

/** 貸出状況の表示文字列を作る（貸出中は返却予定日と期限超過を併記する） */
function describeAvailability(equipment: EquipmentResponse): string {
  if (equipment.availability === 'available') {
    return AVAILABILITY_LABELS.available
  }
  const dueText = `返却予定 ${formatDate(equipment.current_due_date)}`
  const overdueText = equipment.is_overdue ? '（期限超過）' : ''
  return `${AVAILABILITY_LABELS.lent}：${dueText}${overdueText}`
}

export function EquipmentListPage() {
  const [form, setForm] = useState<SearchForm>(EMPTY_FORM)
  const [condition, setCondition] = useState<SearchForm>(EMPTY_FORM)
  const [page, setPage] = useState(1)

  const categories = useFetch(fetchCategories)

  const loadEquipments = useCallback(() => {
    const query: EquipmentListQuery = {
      keyword: condition.keyword,
      category: condition.category,
      availability: condition.availability === '' ? undefined : condition.availability,
      page,
      page_size: PAGE_SIZE,
    }
    return fetchEquipments(query)
  }, [condition, page])
  const equipments = useFetch(loadEquipments)

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setCondition(form)
    setPage(1)
  }

  function handleClear() {
    setForm(EMPTY_FORM)
    setCondition(EMPTY_FORM)
    setPage(1)
  }

  const categoryItems = categories.data?.items ?? []

  return (
    <section>
      <h2>備品一覧</h2>
      <form onSubmit={handleSearch} className="card search-form">
        <div className="form-field">
          <label htmlFor="search-keyword">キーワード（資産番号・備品名）</label>
          <input
            id="search-keyword"
            type="text"
            maxLength={50}
            value={form.keyword}
            onChange={(event) => setForm({ ...form, keyword: event.target.value })}
          />
        </div>
        <div className="form-field">
          <label htmlFor="search-category">分類</label>
          <select
            id="search-category"
            value={form.category}
            onChange={(event) => setForm({ ...form, category: event.target.value })}
          >
            <option value="">指定なし</option>
            {categoryItems.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label htmlFor="search-availability">貸出状況</label>
          <select
            id="search-availability"
            value={form.availability}
            onChange={(event) =>
              setForm({ ...form, availability: event.target.value as SearchForm['availability'] })
            }
          >
            <option value="">指定なし</option>
            <option value="available">{AVAILABILITY_LABELS.available}</option>
            <option value="lent">{AVAILABILITY_LABELS.lent}</option>
          </select>
        </div>
        <div className="button-row">
          <button type="submit">検索</button>
          <button type="button" className="secondary" onClick={handleClear}>
            条件をクリア
          </button>
        </div>
      </form>

      {equipments.status === 'loading' ? <Loading /> : null}
      {equipments.status === 'error' ? (
        <>
          <ErrorMessage message={equipments.error} />
          <button type="button" onClick={equipments.reload}>
            再読み込み
          </button>
        </>
      ) : null}
      {equipments.status === 'success' && equipments.data !== null ? (
        <>
          {equipments.data.items.length === 0 ? (
            <p>条件に一致する備品がありません。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>資産番号</th>
                  <th>備品名</th>
                  <th>分類</th>
                  <th>保管場所</th>
                  <th>貸出状況</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {equipments.data.items.map((equipment) => (
                  <tr key={equipment.id}>
                    <td>{equipment.asset_number}</td>
                    <td>{equipment.name}</td>
                    <td>{equipment.category}</td>
                    <td>{equipment.location}</td>
                    <td>{describeAvailability(equipment)}</td>
                    <td>
                      <Link to={`/equipments/${equipment.id}`}>詳細・申請</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pagination
            page={equipments.data.page}
            pageSize={equipments.data.page_size}
            total={equipments.data.total}
            onChange={setPage}
          />
        </>
      ) : null}
    </section>
  )
}
