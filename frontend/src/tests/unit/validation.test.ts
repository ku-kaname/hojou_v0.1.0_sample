/**
 * 単体テスト：入力チェック（パスワード変更・貸出申請）
 *
 * テスト仕様書：単体テスト仕様書/frontend/共通/入力チェック・整形・API共通（項番1〜14）
 * 設計書：要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更）
 * テスト対象ファイル：frontend/src/utils/validation.ts
 *
 * 【テストの考え方】
 * 画面を使わず、入力値を関数へ渡して「エラーメッセージ」または「null（問題なし）」を確認する。
 */

import { validateLoanRequestInput, validatePasswordInput } from '../../utils/validation'

describe('validatePasswordInput', () => {
  const current = 'old-password'

  it('項番1：新しいパスワードが7文字ならエラー', () => {
    expect(validatePasswordInput(current, '1234567', '1234567')).toBe(
      '新しいパスワードは8文字以上で入力してください',
    )
  })

  it('項番2：新しいパスワードが8文字ならOK（最小文字数ちょうど）', () => {
    expect(validatePasswordInput(current, '12345678', '12345678')).toBeNull()
  })

  it('項番3：確認用と一致しなければエラー', () => {
    expect(validatePasswordInput(current, 'new-password-1', 'new-password-2')).toBe(
      '新しいパスワードと確認用の入力が一致しません',
    )
  })

  it('項番4：現在のパスワードと同じならエラー', () => {
    expect(validatePasswordInput('same-password', 'same-password', 'same-password')).toBe(
      '現在のパスワードと異なるパスワードを入力してください',
    )
  })

  it('項番5：誤りが複数あるときは、先に判定する文字数のメッセージを返す', () => {
    // 7文字（短い）かつ確認用と不一致
    expect(validatePasswordInput(current, '1234567', 'abcdefg')).toBe(
      '新しいパスワードは8文字以上で入力してください',
    )
  })
})

describe('validateLoanRequestInput', () => {
  // テストで「今日」として使う日付
  const today = '2026-09-26'

  it('項番6：開始日または返却予定日が空ならエラー', () => {
    const message = '開始日と返却予定日を入力してください'
    expect(validateLoanRequestInput('', '2026-09-27', '会議', today)).toBe(message)
    expect(validateLoanRequestInput('2026-09-27', '', '会議', today)).toBe(message)
  })

  it('項番7：開始日が今日の前日ならエラー', () => {
    expect(validateLoanRequestInput('2026-09-25', '2026-09-26', '会議', today)).toBe(
      '開始日は今日以降の日付を指定してください',
    )
  })

  it('項番8：開始日が今日ならOK（今日ちょうど）', () => {
    expect(validateLoanRequestInput('2026-09-26', '2026-09-26', '会議', today)).toBeNull()
  })

  it('項番9：返却予定日が開始日の前日ならエラー', () => {
    expect(validateLoanRequestInput('2026-09-27', '2026-09-26', '会議', today)).toBe(
      '返却予定日は開始日以降の日付を指定してください',
    )
  })

  it('項番10：返却予定日が開始日と同じならOK', () => {
    expect(validateLoanRequestInput('2026-09-27', '2026-09-27', '会議', today)).toBeNull()
  })

  it('項番11：用途が空白だけならエラー', () => {
    expect(validateLoanRequestInput('2026-09-26', '2026-09-26', '   ', today)).toBe(
      '用途を入力してください',
    )
  })

  it('項番12：用途が200文字ならOK（最大文字数ちょうど）', () => {
    const purpose = 'あ'.repeat(200)
    expect(validateLoanRequestInput('2026-09-26', '2026-09-26', purpose, today)).toBeNull()
  })

  it('項番13：用途が201文字ならエラー', () => {
    const purpose = 'あ'.repeat(201)
    expect(validateLoanRequestInput('2026-09-26', '2026-09-26', purpose, today)).toBe(
      '用途は200文字以内で入力してください',
    )
  })

  it('項番14：用途の前後の空白は文字数に含めない', () => {
    // 前後に空白を付けると合計は204文字だが、実質200文字なのでOK
    const purpose = `  ${'あ'.repeat(200)}  `
    expect(validateLoanRequestInput('2026-09-26', '2026-09-26', purpose, today)).toBeNull()
  })
})
