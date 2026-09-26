# フロントエンド共通（入力チェック・整形・API共通処理） 単体テスト仕様書

| 項目 | 内容 |
|---|---|
| ファイル名 | frontend/src/tests/unit/validation.test.ts／common.test.ts／client.test.ts |
| クラス名 | - |
| メソッド名 | validatePasswordInput／validateLoanRequestInput／formatDate／formatDateTime／toDateInputValue／calcTotalPages／isCancelable／notificationLink／buildQueryString／requestJson／apiDownload／toErrorMessage |
| 作成日 | 2026-09-26 |
| 最終更新日 | 2026-09-26 |

| 項番 | 大項目 | 中項目 | 小項目 | 詳細 | 観点 | 対応設計書 | 対応箇所 | テスト内容 | 想定結果 | エビデンス | 担当者 | 実施日 | 実施結果 | 確認者 | 確認日 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 入力チェック | validatePasswordInput | 境界値 | 新しいパスワードが7文字 | 境界値 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 新しいパスワード7文字・確認用も同じ・現在のパスワードと異なる値を渡す | 「新しいパスワードは8文字以上で入力してください」を返す |  |  |  |  |  |  |
| 2 | 入力チェック | validatePasswordInput | 境界値 | 新しいパスワードが8文字 | 境界値 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 新しいパスワード8文字・確認用も同じ・現在のパスワードと異なる値を渡す | nullを返す |  |  |  |  |  |  |
| 3 | 入力チェック | validatePasswordInput | 異常系 | 確認用と不一致 | 条件分岐 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 新しいパスワードと確認用が異なる値を渡す | 「新しいパスワードと確認用の入力が一致しません」を返す |  |  |  |  |  |  |
| 4 | 入力チェック | validatePasswordInput | 異常系 | 現在のパスワードと同じ | 条件分岐 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 現在のパスワードと新しいパスワード（確認用も）が同じ値を渡す | 「現在のパスワードと異なるパスワードを入力してください」を返す |  |  |  |  |  |  |
| 5 | 入力チェック | validatePasswordInput | 正常系 | 複数の誤りがある場合の優先順位 | 条件分岐 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 8文字未満かつ確認用と不一致の値を渡す | 先に判定する文字数のメッセージを返す |  |  |  |  |  |  |
| 6 | 入力チェック | validateLoanRequestInput | 異常系 | 日付が未入力 | 条件分岐 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 開始日または返却予定日が空文字の値を渡す | 「開始日と返却予定日を入力してください」を返す |  |  |  |  |  |  |
| 7 | 入力チェック | validateLoanRequestInput | 境界値 | 開始日が今日より前 | 境界値 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 開始日が今日の前日の値を渡す | 「開始日は今日以降の日付を指定してください」を返す |  |  |  |  |  |  |
| 8 | 入力チェック | validateLoanRequestInput | 境界値 | 開始日が今日 | 境界値 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 開始日が今日・返却予定日が今日・用途ありの値を渡す | nullを返す |  |  |  |  |  |  |
| 9 | 入力チェック | validateLoanRequestInput | 境界値 | 返却予定日が開始日より前 | 境界値 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 返却予定日が開始日の前日の値を渡す | 「返却予定日は開始日以降の日付を指定してください」を返す |  |  |  |  |  |  |
| 10 | 入力チェック | validateLoanRequestInput | 境界値 | 返却予定日が開始日と同じ | 境界値 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 返却予定日＝開始日の値を渡す | nullを返す |  |  |  |  |  |  |
| 11 | 入力チェック | validateLoanRequestInput | 異常系 | 用途が空白のみ | 条件分岐 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 用途が空白のみの値を渡す | 「用途を入力してください」を返す |  |  |  |  |  |  |
| 12 | 入力チェック | validateLoanRequestInput | 境界値 | 用途が200文字 | 境界値 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 用途が200文字の値を渡す | nullを返す |  |  |  |  |  |  |
| 13 | 入力チェック | validateLoanRequestInput | 境界値 | 用途が201文字 | 境界値 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 用途が201文字の値を渡す | 「用途は200文字以内で入力してください」を返す |  |  |  |  |  |  |
| 14 | 入力チェック | validateLoanRequestInput | 境界値 | 用途の前後の空白は文字数に含めない | 境界値 | 要件定義書/画面一覧（S04 備品詳細・S06 パスワード変更） | utils/validation.ts | 前後に空白を付けて実質200文字になる用途を渡す | nullを返す |  |  |  |  |  |  |
| 15 | 整形 | formatDate | 正常系 | 日付の整形 | 条件分岐 | 要件定義書/画面一覧（S02・S05・S07〜S11） | utils/format.ts | 「2026-09-26」を渡す | 「2026/09/26」を返す |  |  |  |  |  |  |
| 16 | 整形 | formatDate | 境界値 | 未指定・空文字 | 境界値 | 要件定義書/画面一覧（S02・S05・S07〜S11） | utils/format.ts | null・undefined・空文字をそれぞれ渡す | いずれも「-」を返す |  |  |  |  |  |  |
| 17 | 整形 | formatDateTime | 正常系 | 日時の整形 | 条件分岐 | 要件定義書/画面一覧（S02・S05・S07〜S11） | utils/format.ts | 「2026-09-26T06:00:00+09:00」を渡す | 「2026/09/26 06:00」を返す |  |  |  |  |  |  |
| 18 | 整形 | formatDateTime | 境界値 | 時刻部分がない・未指定 | 境界値 | 要件定義書/画面一覧（S02・S05・S07〜S11） | utils/format.ts | 「2026-09-26」・nullをそれぞれ渡す | 「2026/09/26」・「-」を返す |  |  |  |  |  |  |
| 19 | 整形 | toDateInputValue | 境界値 | 月・日のゼロ埋め | 境界値 | 要件定義書/画面一覧（S02・S05・S07〜S11） | utils/format.ts | 2026年1月5日のDateを渡す | 「2026-01-05」を返す |  |  |  |  |  |  |
| 20 | 整形 | calcTotalPages | 境界値 | 総ページ数の計算 | 計算 | 要件定義書/画面一覧（S02・S05・S07〜S11） | utils/pagination.ts | 総件数0、20、21、40、41（ページサイズ20）を渡す | 1、1、2、2、3を返す（最小1） |  |  |  |  |  |  |
| 21 | 整形 | calcTotalPages | 異常系 | ページサイズが0以下 | 境界値 | 要件定義書/画面一覧（S02・S05・S07〜S11） | utils/pagination.ts | ページサイズ0を渡す | 1を返す |  |  |  |  |  |  |
| 22 | 整形 | isCancelable | 正常系 | 取消できる状態 | 条件分岐 | 要件定義書/画面一覧（S02・S05・S07〜S11） | utils/format.ts | 6種類の申請状態をそれぞれ渡す | 承認待ち（requested）・承認済み（approved）のみtrue、その他はfalse |  |  |  |  |  |  |
| 23 | 整形 | notificationLink | 正常系 | 通知種別ごとの移動先 | 条件分岐 | 要件定義書/画面一覧（S02・S05・S07〜S11） | utils/format.ts | 新規申請・期限超過（管理者／一般）・承認の種別を渡す | 新規申請は/admin/requests、期限超過は管理者のみ/admin/overdue、その他は/my-requests |  |  |  |  |  |  |
| 24 | API共通処理 | buildQueryString | 正常系 | 値の変換 | 条件分岐 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts buildQueryString | 文字列・数値・0・falseを含むパラメーターを渡す | 0・falseも含めてURL形式の文字列に変換する |  |  |  |  |  |  |
| 25 | API共通処理 | buildQueryString | 境界値 | 未指定・空文字・null・パラメーターなし | 境界値 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts buildQueryString | undefined・null・空文字を含む値、パラメーターなしをそれぞれ渡す | 該当項目を除外し、指定が無ければ空文字を返す |  |  |  |  |  |  |
| 26 | API共通処理 | requestJson | 正常系 | 認証ヘッダーの付与 | セキュリティ | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts send／readError | トークンを保管した状態でAPIを呼び出す | Authorizationヘッダーに「Bearer トークン」を付与する |  |  |  |  |  |  |
| 27 | API共通処理 | requestJson | 正常系 | トークン未保管・認証不要の呼び出し | セキュリティ | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts send／readError | トークン未保管、およびトークンを保管した状態で認証不要（skipAuth）として呼び出す | いずれもAuthorizationヘッダーを付与しない |  |  |  |  |  |  |
| 28 | API共通処理 | requestJson | 正常系 | 204レスポンス | 条件分岐 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts send／readError | ステータス204（本文なし）を返すAPIを呼び出す | undefinedを返す |  |  |  |  |  |  |
| 29 | API共通処理 | requestJson | 異常系 | detailが文字列のエラー | 例外処理 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts send／readError | ステータス400・detail「メッセージ」のレスポンスを返す | ApiError（ステータス400・メッセージ＝detail）を投げる |  |  |  |  |  |  |
| 30 | API共通処理 | requestJson | 異常系 | 入力検証エラー（detailが配列） | 例外処理 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts send／readError | ステータス422・detailが配列のレスポンスを返す | ApiErrorのメッセージが「入力内容に誤りがあります」 |  |  |  |  |  |  |
| 31 | API共通処理 | requestJson | 異常系 | 本文がJSONでないエラー | 例外処理 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts send／readError | ステータス500・本文がJSONでないレスポンスを返す | ApiErrorのメッセージが「予期しないエラーが発生しました」 |  |  |  |  |  |  |
| 32 | API共通処理 | requestJson | 異常系 | CSVの行別エラー | 例外処理 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts send／readError | ステータス400・detailとerrors（行番号・列・内容）を返す | ApiErrorのrowErrorsにerrorsの内容が入る |  |  |  |  |  |  |
| 33 | API共通処理 | requestJson | 異常系 | 通信失敗 | 例外処理 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts send／readError | fetchが例外を投げる状態でAPIを呼び出す | ApiError（ステータス0・「サーバーに接続できません」から始まるメッセージ）を投げる |  |  |  |  |  |  |
| 34 | API共通処理 | requestJson | 異常系 | 401受信時のトークン破棄 | セキュリティ | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts send／readError | トークンを保管した状態で401を返す。認証不要の呼び出しでも401を返す | 認証ありではトークンを破棄し未認証時の処理を1回呼ぶ。認証不要では破棄も呼び出しもしない |  |  |  |  |  |  |
| 35 | API共通処理 | apiDownload | 正常系 | ファイル名の取り出し（UTF-8指定） | 条件分岐 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts extractFilename | Content-Dispositionが filename*=UTF-8''（URLエンコード）のレスポンスを返す | デコードしたファイル名を返す |  |  |  |  |  |  |
| 36 | API共通処理 | apiDownload | 正常系 | ファイル名の取り出し（通常指定） | 条件分岐 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts extractFilename | Content-Dispositionが filename="名前.csv" のレスポンスを返す | 「名前.csv」を返す |  |  |  |  |  |  |
| 37 | API共通処理 | apiDownload | 境界値 | ファイル名なし・デコード不可 | 境界値 | 設計書/エンドポイント（エラーレスポンス・認証） | api/client.ts extractFilename | Content-Dispositionなし、およびURLデコードできない値のレスポンスを返す | 指定した既定のファイル名を返す |  |  |  |  |  |  |
| 38 | API共通処理 | toErrorMessage | 正常系 | 例外からのメッセージ取り出し | 条件分岐 | 設計書/エンドポイント（エラーレスポンス・認証） | utils/error.ts | ApiError・通常のError・文字列をそれぞれ渡す | ApiErrorはそのメッセージ、それ以外は「予期しないエラーが発生しました」 |  |  |  |  |  |  |
