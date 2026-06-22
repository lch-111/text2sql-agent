import React from 'react'
import * as ReactDOM from 'react-dom'
import { createRoot, StrictMode } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.jsx'

// 暴露 React / ReactDOM 给 window，供 CDN UMD 库在工厂函数中引用
window.React = React
window.ReactDOM = ReactDOM

// 动态注入 react-draggable + react-grid-layout 的 CDN UMD 脚本（此时 React/ReactDOM 已就绪）
;(function loadCDNLibs() {
  const css = document.createElement('link')
  css.rel = 'stylesheet'
  css.href = 'https://unpkg.com/react-grid-layout@1.4.4/css/styles.css'
  document.head.appendChild(css)
  const urls = [
    'https://unpkg.com/react-draggable@4.4.6/dist/react-draggable.min.js',
    'https://unpkg.com/react-grid-layout@1.4.4/dist/react-grid-layout.min.js',
  ]
  urls.forEach(src => {
    const s = document.createElement('script')
    s.src = src; s.async = false; document.body.appendChild(s)
  })
})()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
