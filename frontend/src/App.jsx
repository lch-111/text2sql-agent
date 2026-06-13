import { useState, useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import Landing from './pages/Landing'
import Chat from './pages/Chat'

function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'light')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme(t => t === 'light' ? 'dark' : 'light')

  return (
    <Routes>
      <Route path="/" element={<Landing theme={theme} toggleTheme={toggleTheme} />} />
      <Route path="/chat" element={<Chat theme={theme} toggleTheme={toggleTheme} />} />
    </Routes>
  )
}

export default App
