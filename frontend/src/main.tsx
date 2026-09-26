/**
 * アプリケーションの起点
 *
 * 【概要】
 * ルート要素へアプリを描画する。ルーティングと認証状態の提供をここで組み込む。
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import { AuthProvider } from './auth/AuthProvider'
import './styles.css'

const rootElement = document.getElementById('root')
if (rootElement === null) {
  throw new Error('描画先の要素（#root）が見つかりません')
}

createRoot(rootElement).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
