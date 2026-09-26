/**
 * 通知変更の通知
 *
 * 【概要】
 * 通知を既読にした際、ヘッダーの未読件数を最新にするため、画面間で変更を知らせる仕組み。
 */

const NOTIFICATIONS_CHANGED_EVENT = 'hojou:notifications-changed'

/** 通知の状態が変わったことを知らせる（既読化した後に呼ぶ） */
export function notifyNotificationsChanged(): void {
  const changedEvent = new Event(NOTIFICATIONS_CHANGED_EVENT)
  window.dispatchEvent(changedEvent)
}

/** 通知の変更を受け取る処理を登録する。戻り値の関数を呼ぶと登録を解除する */
export function subscribeNotificationsChanged(listener: () => void): () => void {
  window.addEventListener(NOTIFICATIONS_CHANGED_EVENT, listener)
  return () => {
    window.removeEventListener(NOTIFICATIONS_CHANGED_EVENT, listener)
  }
}
