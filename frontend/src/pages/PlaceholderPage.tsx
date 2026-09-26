/**
 * 仮画面
 *
 * 【概要】
 * 個別画面の実装（バックログ「フロントエンド一般ユーザー画面」「フロントエンド管理者画面」）までの間、
 * ルーティングと権限制御の確認用として表示する仮の画面。各画面の実装時に置き換える。
 */

interface PlaceholderPageProps {
  title: string
}

export function PlaceholderPage({ title }: PlaceholderPageProps) {
  return (
    <section>
      <h2>{title}</h2>
      <p>この画面は準備中です。</p>
    </section>
  )
}
