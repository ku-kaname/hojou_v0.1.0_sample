/**
 * 備品API（参照）
 *
 * 【概要】
 * 備品の一覧検索・詳細取得・分類一覧取得・予約状況取得のAPIを呼び出す。
 */

import type {
  CategoryListResponse,
  EquipmentListQuery,
  EquipmentResponse,
  Page,
  ReservationListResponse,
} from '../types/api'
import { apiGet } from './client'

/** 備品を検索して一覧取得する */
export function fetchEquipments(query: EquipmentListQuery): Promise<Page<EquipmentResponse>> {
  return apiGet<Page<EquipmentResponse>>('/equipments', { ...query })
}

/** 備品を1件取得する */
export function fetchEquipment(equipmentId: number): Promise<EquipmentResponse> {
  return apiGet<EquipmentResponse>(`/equipments/${equipmentId}`)
}

/** 検索条件の選択肢となる分類の一覧を取得する */
export function fetchCategories(): Promise<CategoryListResponse> {
  return apiGet<CategoryListResponse>('/equipments/categories')
}

/** 備品の予約状況（承認済み・貸出中の期間）を取得する */
export function fetchReservations(equipmentId: number): Promise<ReservationListResponse> {
  return apiGet<ReservationListResponse>(`/equipments/${equipmentId}/reservations`)
}
