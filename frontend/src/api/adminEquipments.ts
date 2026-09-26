/**
 * 備品API（管理者向け）
 *
 * 【概要】
 * 備品の登録・編集（無効化を含む）とCSV一括登録のAPIを呼び出す。
 */

import type {
  CsvImportResponse,
  EquipmentCreateRequest,
  EquipmentResponse,
  EquipmentUpdateRequest,
} from '../types/api'
import { apiPost, apiPut, apiUpload } from './client'

/** 備品を登録する（資産番号が重複する場合はエラー） */
export function createEquipment(request: EquipmentCreateRequest): Promise<EquipmentResponse> {
  return apiPost<EquipmentResponse>('/admin/equipments', request)
}

/** 備品を編集する（有効フラグによる無効化・再有効化を含む） */
export function updateEquipment(
  equipmentId: number,
  request: EquipmentUpdateRequest,
): Promise<EquipmentResponse> {
  return apiPut<EquipmentResponse>(`/admin/equipments/${equipmentId}`, request)
}

/** CSVファイルから備品を一括登録する（全件検証後に登録。誤りがあればApiErrorのrowErrorsに行ごとの内容が入る） */
export function importEquipmentsCsv(file: File): Promise<CsvImportResponse> {
  return apiUpload<CsvImportResponse>('/admin/equipments/import', file)
}
