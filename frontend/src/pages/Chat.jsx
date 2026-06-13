import { useState, lazy, Suspense } from 'react'
import { motion } from 'framer-motion'
import HistorySidebar from '../components/HistorySidebar'
import ToolsSidebar from '../components/ToolsSidebar'
import ChatArea from '../components/ChatArea'

// TabPanels 懒加载（包含 ECharts，1.5MB）
const TabPanels = lazy(() => import('../components/TabPanels'))

const TABS = [
  { key: 'chat', label: '💬 对话' },
  { key: 'dashboard', label: '📊 大屏' },
  { key: 'monitor', label: '⚙️ 监控' },
  { key: 'tables', label: '📋 表格' },
  { key: 'eval', label: '📋 评估' },
]

export default function Chat({ theme, toggleTheme }) {
  const [activeTab, setActiveTab] = useState('chat')
  const [leftOpen, setLeftOpen] = useState(true)
  const [rightOpen, setRightOpen] = useState(true)
  const [chatKey, setChatKey] = useState(Date.now())

  const newChat = () => {
    const cid = Date.now()
    // 创建新对话历史条目（仅手动新建才创建卡片）
    try {
      const history = JSON.parse(localStorage.getItem('chat_history') || '[]')
      history.push({ id: cid, question: '新对话', preview: '新对话', time: new Date().toLocaleString('zh-CN'), sql: '', resultSummary: '', resultCount: 0 })
      localStorage.setItem('chat_history', JSON.stringify(history))
    } catch {}
    // 清空当前对话消息，触发 ChatArea 重新挂载
    try { localStorage.removeItem('chat_messages') } catch {}
    setChatKey(cid)
    // 通知侧栏刷新
    window.dispatchEvent(new Event('chat-history-changed'))
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}
    >
      {/* Header */}
      <header style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 20px',
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border-color)',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <h1 style={{ fontSize: 16, fontWeight: 500, color: 'var(--text-primary)' }}>
            🐱 智能数据分析指挥中心
          </h1>
          <button onClick={newChat} style={{
            padding: '4px 12px', fontSize: 12, cursor: 'pointer',
            background: 'var(--bg-input)', border: '1px solid var(--border-color)',
            borderRadius: 6, color: 'var(--text-muted)',
          }}>
            ✨ 新建对话
          </button>
        </div>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Text-to-SQL Agent
        </span>
        <button onClick={toggleTheme} style={{
          background: 'none', border: '1px solid var(--border-color)',
          borderRadius: 6, padding: '4px 10px',
          color: 'var(--text-muted)', cursor: 'pointer', fontSize: 13,
        }}>
          {theme === 'light' ? '🌙' : '☀️'}
        </button>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left sidebar */}
        <HistorySidebar open={leftOpen} onToggle={() => setLeftOpen(!leftOpen)} />

        {/* Main content */}
        <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Tab bar */}
          <nav style={{
            display: 'flex', gap: 2, padding: '0 16px',
            background: 'var(--bg-secondary)',
            borderBottom: '1px solid var(--border-color)',
            flexShrink: 0,
          }}>
            {TABS.map(tab => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                style={{
                  padding: '10px 16px', fontSize: 13,
                  background: activeTab === tab.key ? 'var(--bg-primary)' : 'transparent',
                  color: activeTab === tab.key ? 'var(--text-primary)' : 'var(--text-muted)',
                  border: 'none', borderBottom: activeTab === tab.key ? '2px solid var(--accent)' : '2px solid transparent',
                  cursor: 'pointer', transition: 'all 0.2s',
                }}
              >
                {tab.label}
              </button>
            ))}
          </nav>

          {/* Tab content */}
          <div style={{ flex: 1, overflow: 'auto' }}>
            {/* 始终渲染 ChatArea，仅切换可见性以保持消息状态 */}
            <div style={{ display: activeTab === 'chat' ? 'block' : 'none', height: '100%' }}>
              <ChatArea key={chatKey} convId={chatKey} />
            </div>
            {activeTab !== 'chat' && (
              <Suspense fallback={<div style={{ padding: 20, color: 'var(--text-muted)', fontSize: 13 }}>加载中...</div>}>
                <TabPanels activeTab={activeTab} />
              </Suspense>
            )}
          </div>
        </main>

        {/* Right sidebar */}
        <ToolsSidebar open={rightOpen} onToggle={() => setRightOpen(!rightOpen)} />
      </div>
    </motion.div>
  )
}
