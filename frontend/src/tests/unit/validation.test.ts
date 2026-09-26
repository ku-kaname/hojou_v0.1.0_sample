import { validateLoanRequestInput, validatePasswordInput } from '../../utils/validation'

describe('validatePasswordInput', () => {
  it('8文字未満はエラー', () => {
    expect(validatePasswordInput('old-password', 'short', 'short')).toContain('8文字以上')
  })

  it('確認用と一致しない場合はエラー', () => {
    expect(validatePasswordInput('old-password', 'new-password-1', 'new-password-2')).toContain(
      '一致しません',
    )
  })

  it('現在と同じパスワードはエラー', () => {
    expect(validatePasswordInput('same-password', 'same-password', 'same-password')).toContain(
      '異なる',
    )
  })

  it('問題なければnull', () => {
    expect(validatePasswordInput('old-password', 'new-password-1', 'new-password-1')).toBeNull()
  })
})

describe('validateLoanRequestInput', () => {
  const today = '2026-09-26'

  it('開始日が過去ならエラー', () => {
    expect(validateLoanRequestInput('2026-09-25', '2026-09-26', '会議', today)).toContain(
      '今日以降',
    )
  })

  it('返却予定日が開始日より前ならエラー', () => {
    expect(validateLoanRequestInput('2026-09-27', '2026-09-26', '会議', today)).toContain(
      '開始日以降',
    )
  })

  it('用途が空白のみならエラー', () => {
    expect(validateLoanRequestInput('2026-09-26', '2026-09-26', '  ', today)).toContain('用途')
  })

  it('問題なければnull', () => {
    expect(validateLoanRequestInput('2026-09-26', '2026-09-28', '会議', today)).toBeNull()
  })
})
