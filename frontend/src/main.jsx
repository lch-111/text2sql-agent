import React from 'react'
import * as ReactDOM from 'react-dom'
import { StrictMode } from 'react'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.jsx'

// 暴露 React / ReactDOM 给 window，供 CDN UMD 库（react-draggable / react-grid-layout）在工厂函数中引用
window.React = React
window.ReactDOM = ReactDOM

// 动态加载 react-draggable + react-grid-layout 的 CDN UMD 脚本等 React 就绪后注入
;(function loadCDNLibs() {
  const css = document.createElement('link')
  css.rel = 'stylesheet'
  css.href = 'https://unpkg.com/react-grid-layout@1.4.4/css/styles.css'
  document.head.appendChild(css)

  const libs = [
    'https://unpkg.com/react-draggable@4.4.6/dist/react-draggable.min.js',
    'https://unpkg.com/react-grid-layout@1.4.4/dist/react-grid-layout.min.js',
  ]
  libs.forEach(url => {
    const s = document.createElement('script')
    s.src = url
    s.async = false  // 按顺序加载
    document.body.appendChild(s)
  })
})()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
