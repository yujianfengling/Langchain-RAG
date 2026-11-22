import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { initDataSource } from './competition/dataSource'
import './index.css'

/** 比赛版：先按 URL 决定数据源（默认官方赛题数据），再渲染。
 *  数据源就绪前在 #root 放最小加载指示——否则慢网络下是整页空白，老师会以为系统坏了。 */
const rootEl = document.getElementById('root')!
rootEl.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;'
  + 'font-family:system-ui,sans-serif;color:#475569;font-size:14px;">正在加载 智慧学伴…</div>'

void initDataSource().finally(() => {
  rootEl.innerHTML = ''
  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
})