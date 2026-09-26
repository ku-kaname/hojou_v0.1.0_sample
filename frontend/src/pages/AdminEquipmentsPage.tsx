/**
 * 備品管理画面（S09）
 *
 * 【概要】
 * 無効の備品も含めて備品を一覧表示し、登録・編集（無効化を含む）・CSV一括登録を行う。
 * 編集時は資産番号を変更できない。CSV一括登録は全件検証され、誤りがある場合は行ごとの内容を表示する。
 */

import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { createEquipment, importEquipmentsCsv, updateEquipment } from '../api/adminEquipments'
import { fetchEquipments } from '../api/equipments'
import { ApiError } from '../api/client'
import { ErrorMessage } from '../components/ErrorMessage'
import { FormField } from '../components/FormField'
import { Loading } from '../components/Loading'
import { Pagination } from '../components/Pagination'
import type { CsvRowError, EquipmentResponse } from '../types/api'
import { toErrorMessage } from '../utils/error'
import { AVAILABILITY_LABELS } from '../utils/format'
import { useFetch } from '../utils/useFetch'

const PAGE_SIZE = 20

/** 入力フォームの状態（editingIdがnullの場合は新規登録） */
interface EquipmentFormState {
  editingId: number | null
  assetNumber: string
  name: string
  category: string
  description: string
  location: string
  isActive: boolean
}

const EMPTY_FORM: EquipmentFormState = {
  editingId: null,
  assetNumber: '',
  name: '',
  category: '',
  description: '',
  location: '',
  isActive: true,
}

/** 一覧の備品から編集フォームの初期値を作る */
function toFormState(equipment: EquipmentResponse): EquipmentFormState {
  return {
    editingId: equipment.id,
    assetNumber: equipment.asset_number,
    name: equipment.name,
    category: equipment.category,
    description: equipment.description,
    location: equipment.location,
    isActive: equipment.is_active,
  }
}

export function AdminEquipmentsPage() {
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [form, setForm] = useState<EquipmentFormState | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [rowErrors, setRowErrors] = useState<CsvRowError[]>([])

  const loadEquipments = useCallback(() => {
    const trimmedKeyword = keyword.trim()
    return fetchEquipments({
      keyword: trimmedKeyword === '' ? undefined : trimmedKeyword,
      include_inactive: true,
      page,
      page_size: PAGE_SIZE,
    })
  }, [keyword, page])
  const equipments = useFetch(loadEquipments)

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setKeyword(keywordInput)
    setPage(1)
  }

  function openCreateForm() {
    setForm(EMPTY_FORM)
    setFormError(null)
    setMessage(null)
  }

  function openEditForm(equipment: EquipmentResponse) {
    const state = toFormState(equipment)
    setForm(state)
    setFormError(null)
    setMessage(null)
  }

  function closeForm() {
    setForm(null)
    setFormError(null)
  }

  function updateForm(changes: Partial<EquipmentFormState>) {
    setForm((current) => (current === null ? current : { ...current, ...changes }))
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (form === null) {
      return
    }
    const name = form.name.trim()
    const category = form.category.trim()
    const description = form.description.trim()
    const location = form.location.trim()
    const assetNumber = form.assetNumber.trim()
    if (form.editingId === null && !/^[A-Za-z0-9-]{1,32}$/.test(assetNumber)) {
      setFormError('資産番号は半角英数字とハイフンで32文字以内で入力してください')
      return
    }
    if (name === '' || category === '') {
      setFormError('備品名と分類を入力してください')
      return
    }
    setSubmitting(true)
    setFormError(null)
    try {
      if (form.editingId === null) {
        await createEquipment({
          asset_number: assetNumber,
          name,
          category,
          description,
          location,
        })
        setMessage('備品を登録しました')
      } else {
        await updateEquipment(form.editingId, {
          name,
          category,
          description,
          location,
          is_active: form.isActive,
        })
        setMessage('備品を更新しました')
      }
      setForm(null)
      equipments.reload()
    } catch (error) {
      const errorText = toErrorMessage(error)
      setFormError(errorText)
    } finally {
      setSubmitting(false)
    }
  }

  function handleFileChange(files: FileList | null) {
    const selected = files !== null && files.length > 0 ? files.item(0) : null
    setImportFile(selected)
  }

  async function handleImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (importFile === null) {
      setImportError('CSVファイルを選択してください')
      return
    }
    setImporting(true)
    setImportError(null)
    setRowErrors([])
    setMessage(null)
    try {
      const result = await importEquipmentsCsv(importFile)
      setMessage(`${result.imported_count}件の備品を登録しました`)
      setImportFile(null)
      equipments.reload()
    } catch (error) {
      const errorText = toErrorMessage(error)
      setImportError(errorText)
      if (error instanceof ApiError) {
        setRowErrors(error.rowErrors)
      }
    } finally {
      setImporting(false)
    }
  }

  return (
    <section>
      <h2>備品管理</h2>

      {message !== null ? (
        <p className="notice" role="status">
          {message}
        </p>
      ) : null}

      <form onSubmit={handleSearch} className="search-form">
        <FormField id="admin-equipment-keyword" label="キーワード">
          <input
            id="admin-equipment-keyword"
            type="text"
            value={keywordInput}
            onChange={(event) => setKeywordInput(event.target.value)}
          />
        </FormField>
        <button type="submit">検索</button>
        <button type="button" onClick={openCreateForm}>
          備品を登録
        </button>
      </form>

      {form !== null ? (
        <form onSubmit={handleSubmit} className="card">
          <h3>{form.editingId === null ? '備品の登録' : '備品の編集'}</h3>
          <ErrorMessage message={formError} />
          <FormField id="equipment-asset-number" label="資産番号" required>
            <input
              id="equipment-asset-number"
              type="text"
              maxLength={32}
              value={form.assetNumber}
              disabled={form.editingId !== null}
              onChange={(event) => updateForm({ assetNumber: event.target.value })}
            />
          </FormField>
          <FormField id="equipment-name" label="備品名" required>
            <input
              id="equipment-name"
              type="text"
              maxLength={100}
              value={form.name}
              onChange={(event) => updateForm({ name: event.target.value })}
            />
          </FormField>
          <FormField id="equipment-category" label="分類" required>
            <input
              id="equipment-category"
              type="text"
              maxLength={50}
              value={form.category}
              onChange={(event) => updateForm({ category: event.target.value })}
            />
          </FormField>
          <FormField id="equipment-description" label="説明">
            <textarea
              id="equipment-description"
              rows={3}
              maxLength={500}
              value={form.description}
              onChange={(event) => updateForm({ description: event.target.value })}
            />
          </FormField>
          <FormField id="equipment-location" label="保管場所">
            <input
              id="equipment-location"
              type="text"
              maxLength={100}
              value={form.location}
              onChange={(event) => updateForm({ location: event.target.value })}
            />
          </FormField>
          {form.editingId !== null ? (
            <FormField id="equipment-active" label="貸出の受付">
              <label>
                <input
                  id="equipment-active"
                  type="checkbox"
                  checked={form.isActive}
                  onChange={(event) => updateForm({ isActive: event.target.checked })}
                />
                有効（チェックを外すと無効化）
              </label>
            </FormField>
          ) : null}
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

      <form onSubmit={handleImport} className="card">
        <h3>CSV一括登録</h3>
        <p>
          列：資産番号・備品名・分類・説明・保管場所（1行目は見出し行、最大1000行・5MBまで）。
          1件でも誤りがある場合は全件登録されません。
        </p>
        <ErrorMessage message={importError} />
        <FormField id="equipment-import-file" label="CSVファイル">
          <input
            id="equipment-import-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => handleFileChange(event.target.files)}
          />
        </FormField>
        <button type="submit" disabled={importing}>
          {importing ? '登録中…' : '一括登録する'}
        </button>
        {rowErrors.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>行</th>
                <th>列</th>
                <th>内容</th>
              </tr>
            </thead>
            <tbody>
              {rowErrors.map((rowError) => (
                <tr key={`${rowError.row_number}-${rowError.column ?? ''}-${rowError.message}`}>
                  <td>{rowError.row_number}</td>
                  <td>{rowError.column ?? '-'}</td>
                  <td>{rowError.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
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
            <p>該当する備品はありません。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>資産番号</th>
                  <th>備品名</th>
                  <th>分類</th>
                  <th>保管場所</th>
                  <th>貸出状況</th>
                  <th>受付</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {equipments.data.items.map((equipment) => (
                  <tr key={equipment.id}>
                    <td>{equipment.asset_number}</td>
                    <td>{equipment.name}</td>
                    <td>{equipment.category}</td>
                    <td>{equipment.location === '' ? '-' : equipment.location}</td>
                    <td>{AVAILABILITY_LABELS[equipment.availability]}</td>
                    <td>{equipment.is_active ? '有効' : '無効'}</td>
                    <td>
                      <button type="button" onClick={() => openEditForm(equipment)}>
                        編集
                      </button>
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
