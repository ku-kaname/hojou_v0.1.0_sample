# テーブル定義（models）

- **工程**: 設計工程
- **作成者**: Claude Sonnet 5
- **作成日**: 2026-09-26
- **最終更新日**: 2026-09-26

---

## 共通事項

- DBはPostgreSQL。物理名は小文字スネークケース。`USER`は予約語のため物理名を`app_user`とする。
- 内部IDは`INTEGER`の自動採番（`GENERATED ALWAYS AS IDENTITY`）。
- 日時列は`TIMESTAMP WITH TIME ZONE`（UTC保存）、日付列は`DATE`（JSTの暦日）。表内の型は`DATETIME`・`DATE`と表記する。
- 列挙値は`VARCHAR`＋`CHECK`制約で保持する（下記「列挙値」）。
- 物理削除は行わない。外部キーは`ON DELETE RESTRICT`（連鎖削除しない）とする。
- 「最小桁・最大桁」は、文字列の文字数を表す。

### 列挙値

| 列挙 | コード | 表示名 | 使用列 |
|---|---|---|---|
| ロール | `general` | 一般 | `app_user.role` |
| ロール | `admin` | 管理者 | `app_user.role` |
| 申請状態 | `requested` | 申請中 | `loan_request.status` |
| 申請状態 | `approved` | 承認済み | `loan_request.status` |
| 申請状態 | `lent` | 貸出中 | `loan_request.status` |
| 申請状態 | `returned` | 返却済み | `loan_request.status` |
| 申請状態 | `rejected` | 却下 | `loan_request.status` |
| 申請状態 | `canceled` | 取消 | `loan_request.status` |
| 通知種別 | `approved` | 承認 | `notification.type` |
| 通知種別 | `rejected` | 却下 | `notification.type` |
| 通知種別 | `canceled` | 取消 | `notification.type` |
| 通知種別 | `new_request` | 新規申請 | `notification.type` |
| 通知種別 | `due_soon` | 返却期限（前日） | `notification.type` |
| 通知種別 | `overdue` | 返却期限（超過） | `notification.type` |

### 列挙値を参照している設計書

列挙値（一次情報は本表）を変更する際は、次の設計書を更新する。

| 設計書 | 参照内容 |
|---|---|
| スキーマ（schemas）「共通」 | ロール |
| サーバー処理（main）「共通」通知生成 | 通知種別 |
| スキーマ（schemas）「認証・ユーザー管理」 | ロール |
| サーバー処理（main）「認証・ユーザー管理」（ユーザー登録・ユーザー編集・ユーザー一覧取得・初期管理者作成） | ロール |
| CRUD「認証・ユーザー管理」（ユーザー作成・ユーザー情報更新・ユーザー一覧取得） | ロール |
| スキーマ（schemas）「備品管理」（予約期間レスポンス） | 申請状態 |
| サーバー処理（main）「備品管理」（予約状況取得） | 申請状態 |
| CRUD「備品管理」（予約期間一覧取得・備品未完了申請件数取得・貸出中申請取得・備品一覧取得） | 申請状態 |
| スキーマ（schemas）「貸出申請・承認」（貸出申請一覧クエリ・申請レスポンス） | 申請状態 |
| サーバー処理（main）「貸出申請・承認」（貸出申請・申請承認・申請却下・申請管理者取消・承認待ち申請一覧取得・自分の申請一覧取得・申請取消） | 申請状態・通知種別 |
| CRUD「貸出申請・承認」（貸出申請作成・貸出申請一覧取得・占有期間重複件数取得・貸出申請承認却下・貸出申請取消・申請者貸出中件数取得・申請者未貸出申請一括取消） | 申請状態 |
| スキーマ（schemas）「貸出・返却・履歴」（貸出履歴レスポンス・通知レスポンス） | 申請状態・通知種別 |
| サーバー処理（main）「貸出・返却・履歴」（貸出処理・返却処理・貸出履歴CSV出力・通知一覧取得・通知サマリー取得） | 申請状態・通知種別・ロール |
| CRUD「貸出・返却・履歴」（貸出申請貸出更新・貸出申請返却更新・貸出申請自動取消・期限超過貸出一覧取得・期限超過貸出中申請取得・貸出中申請返却予定日指定取得・自動取消対象申請ロック取得・状態別申請件数取得） | 申請状態 |
| 日次処理（scheduler）「日次処理実行」 | 申請状態・通知種別 |
| 各機能群の設計書（機能群ごとの設計時に追記する） | ロール・申請状態・通知種別 |

- 要件定義書「通知」の種別「返却期限」は、返却予定日の前日通知（`due_soon`）と期限超過通知（`overdue`）に分けて保持する（画面の文言を出し分けるため）。

---

## app_user（ユーザー）

| 論理名 | 物理名 | 型 | 必須 | 最小値 | 最大値 | 最小桁 | 最大桁 | 主キー | 外部キー | 制約 | インデックス | 備考 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 内部ID | id | INTEGER | ○ | 1 | - | - | - | ○ | - | 自動採番 | - | グローバルな連番 |
| ユーザーID | login_id | VARCHAR | ○ | - | - | 3 | 32 | - | - | UNIQUE、`^[A-Za-z0-9_-]+$` | 一意インデックス | ログイン用。大文字小文字を区別する。無効化後も再利用不可（物理削除しないため一意制約で担保） |
| 氏名 | name | VARCHAR | ○ | - | - | 1 | 50 | - | - | - | - | - |
| 所属 | department | VARCHAR | - | - | - | 0 | 50 | - | - | - | - | 空文字を許容（NULLにしない）。デフォルト空文字 |
| パスワードハッシュ | password_hash | VARCHAR | ○ | - | - | - | 255 | - | - | - | - | Argon2id。平文は保存しない |
| ロール | role | VARCHAR | ○ | - | - | - | 16 | - | - | CHECK（`general`／`admin`） | - | デフォルト`general` |
| 有効フラグ | is_active | BOOLEAN | ○ | - | - | - | - | - | - | - | - | デフォルトTRUE。無効化＝論理削除 |
| 初回パスワード変更要否 | must_change_password | BOOLEAN | ○ | - | - | - | - | - | - | - | - | デフォルトTRUE（登録時・初期化時）。変更完了でFALSE |
| トークン世代 | token_generation | INTEGER | ○ | 0 | - | - | - | - | - | CHECK（0以上） | - | デフォルト0。失効時に+1 |
| 連続認証失敗回数 | failed_login_count | INTEGER | ○ | 0 | - | - | - | - | - | CHECK（0以上） | - | デフォルト0。成功時・ロック解除時に0へ戻す |
| ロック解除日時 | locked_until | DATETIME | - | - | - | - | - | - | - | - | - | NULL＝ロックなし。連続失敗5回で現在の15分後を設定 |
| 作成日時 | created_at | DATETIME | ○ | - | - | - | - | - | - | - | - | サーバー時刻 |
| 更新日時 | updated_at | DATETIME | ○ | - | - | - | - | - | - | - | - | サーバー時刻。更新時に自動更新 |

### 補足
- ユーザーIDの一意制約は、無効化済みユーザーも対象とする（部分インデックスにしない）。
- 「最後の有効な管理者」の判定はアプリケーション（サービス層）で行う。管理者ロールのレコードを行ロック（`FOR UPDATE`）したうえで、有効な管理者の件数を数える。

---

## equipment（備品）

| 論理名 | 物理名 | 型 | 必須 | 最小値 | 最大値 | 最小桁 | 最大桁 | 主キー | 外部キー | 制約 | インデックス | 備考 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 内部ID | id | INTEGER | ○ | 1 | - | - | - | ○ | - | 自動採番 | - | グローバルな連番 |
| 資産番号 | asset_number | VARCHAR | ○ | - | - | 1 | 32 | - | - | UNIQUE、`^[A-Za-z0-9-]+$` | 一意インデックス | 無効化済みも含めて一意 |
| 備品名 | name | VARCHAR | ○ | - | - | 1 | 100 | - | - | - | - | - |
| 分類 | category | VARCHAR | ○ | - | - | 1 | 50 | - | - | - | 通常インデックス | 自由入力。検索・分類一覧取得で使用 |
| 説明 | description | VARCHAR | - | - | - | 0 | 500 | - | - | - | - | 空文字を許容。デフォルト空文字 |
| 保管場所 | location | VARCHAR | - | - | - | 0 | 100 | - | - | - | - | 空文字を許容。デフォルト空文字 |
| 有効フラグ | is_active | BOOLEAN | ○ | - | - | - | - | - | - | - | - | デフォルトTRUE。無効化＝論理削除 |
| 作成日時 | created_at | DATETIME | ○ | - | - | - | - | - | - | - | - | サーバー時刻 |
| 更新日時 | updated_at | DATETIME | ○ | - | - | - | - | - | - | - | - | サーバー時刻。更新時に自動更新 |

---

## loan_request（貸出申請）

貸出履歴を兼ねる。

| 論理名 | 物理名 | 型 | 必須 | 最小値 | 最大値 | 最小桁 | 最大桁 | 主キー | 外部キー | 制約 | インデックス | 備考 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 内部ID | id | INTEGER | ○ | 1 | - | - | - | ○ | - | 自動採番 | - | グローバルな連番 |
| 備品（内部ID） | equipment_id | INTEGER | ○ | - | - | - | - | - | equipment.id | - | 通常インデックス | - |
| 申請者（ユーザー内部ID） | requester_id | INTEGER | ○ | - | - | - | - | - | app_user.id | - | 通常インデックス | - |
| 開始日 | start_date | DATE | ○ | - | - | - | - | - | - | - | - | JSTの暦日 |
| 返却予定日 | due_date | DATE | ○ | - | - | - | - | - | - | CHECK（`due_date >= start_date`） | - | JSTの暦日 |
| 用途 | purpose | VARCHAR | ○ | - | - | 1 | 200 | - | - | - | - | - |
| 状態 | status | VARCHAR | ○ | - | - | - | 16 | - | - | CHECK（`requested`／`approved`／`lent`／`returned`／`rejected`／`canceled`） | 通常インデックス | デフォルト`requested` |
| 却下・取消理由 | reason | VARCHAR | - | - | - | 0 | 200 | - | - | - | - | 空文字を許容。却下・管理者取消時は必須（アプリケーションで検証） |
| 承認・却下した管理者（ユーザー内部ID） | decided_by | INTEGER | - | - | - | - | - | - | app_user.id | - | - | NULL＝未処理 |
| 貸出処理した管理者（ユーザー内部ID） | lent_by | INTEGER | - | - | - | - | - | - | app_user.id | - | - | NULL＝未貸出 |
| 返却処理した管理者（ユーザー内部ID） | returned_by | INTEGER | - | - | - | - | - | - | app_user.id | - | - | NULL＝未返却 |
| 取消した者（ユーザー内部ID） | canceled_by | INTEGER | - | - | - | - | - | - | app_user.id | - | - | NULL＝未取消または自動取消 |
| 申請日時 | requested_at | DATETIME | ○ | - | - | - | - | - | - | - | - | サーバー時刻 |
| 承認却下日時 | decided_at | DATETIME | - | - | - | - | - | - | - | - | - | NULL＝未処理 |
| 貸出日時 | lent_at | DATETIME | - | - | - | - | - | - | - | - | - | NULL＝未貸出 |
| 返却日時 | returned_at | DATETIME | - | - | - | - | - | - | - | - | - | NULL＝未返却 |
| 取消日時 | canceled_at | DATETIME | - | - | - | - | - | - | - | - | - | NULL＝未取消 |
| 返却時状態メモ | return_note | VARCHAR | - | - | - | 0 | 200 | - | - | - | - | 空文字を許容。デフォルト空文字 |

### 補足（インデックス・排他制約）

| No | 名称 | 種別 | 定義 | 目的 |
|---|---|---|---|---|
| 1 | `ix_loan_request_equipment_status` | 通常インデックス | `(equipment_id, status)` | 備品ごとの承認済み・貸出中の期間検索 |
| 2 | `ix_loan_request_requester` | 通常インデックス | `(requester_id, requested_at)` | 自分の申請一覧 |
| 3 | `ix_loan_request_status_due` | 通常インデックス | `(status, due_date)` | 期限超過一覧・日次処理 |
| 4 | `uq_loan_request_equipment_lent` | 部分一意インデックス | `(equipment_id) WHERE status = 'lent'` | 同一備品の貸出中は1件のみ |
| 5 | `ex_loan_request_equipment_period` | 排他制約（EXCLUDE USING gist） | `(equipment_id WITH =, daterange(start_date, due_date, '[]') WITH &&) WHERE (status IN ('approved', 'lent'))` | 承認済み・貸出中の予定期間の重複を防止（二重貸出の最終防衛線） |

- 排他制約（No.5）は予定期間（開始日〜返却予定日）で判定する。期限超過中の貸出中が「今日」まで占有することによる重複は、アプリケーション（サービス層）が備品の行ロックのもとで検証する。
- 申請中の申請は排他制約の対象外（期間重複を許可する）。

---

## notification（通知）

| 論理名 | 物理名 | 型 | 必須 | 最小値 | 最大値 | 最小桁 | 最大桁 | 主キー | 外部キー | 制約 | インデックス | 備考 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 内部ID | id | INTEGER | ○ | 1 | - | - | - | ○ | - | 自動採番 | - | グローバルな連番 |
| 宛先（ユーザー内部ID） | recipient_id | INTEGER | ○ | - | - | - | - | - | app_user.id | - | 複合インデックス（`recipient_id, is_read, created_at`） | - |
| 種別 | type | VARCHAR | ○ | - | - | - | 16 | - | - | CHECK（`approved`／`rejected`／`canceled`／`new_request`／`due_soon`／`overdue`） | - | - |
| 関連申請（内部ID） | loan_request_id | INTEGER | ○ | - | - | - | - | - | loan_request.id | - | 通常インデックス | - |
| 既読フラグ | is_read | BOOLEAN | ○ | - | - | - | - | - | - | - | - | デフォルトFALSE |
| 作成日時 | created_at | DATETIME | ○ | - | - | - | - | - | - | - | - | サーバー時刻 |
| 通知日 | notified_date | DATE | ○ | - | - | - | - | - | - | - | - | 通知を生成したJSTの日付。全種別で設定する（単発通知も生成日を設定）。日次通知の冪等化に用いる |

### 補足（日次通知の冪等化）
- `uq_notification_daily`：一意インデックス `(recipient_id, type, loan_request_id, notified_date) WHERE type IN ('due_soon', 'overdue')`。日次処理を同日に再実行しても、同じ通知が重複生成されない。

---

## 根拠・関連資料

- 要件定義書「機能要件」（扱うデータ・関連データ削除時の動作・業務ルール）。
- 判断根拠書No.14（二重貸出の防止ルール）。
- 本設計の設計判断：行ロック（アプリケーション）とDB排他制約・部分一意インデックスを併用する二重貸出の防止。
