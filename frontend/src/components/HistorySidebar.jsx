import { motion } from 'framer-motion'
import { useState, useEffect } from 'react'

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem('chat_history') || '[]')
  } catch { return [] }
}

function textSimilarity(a, b) {
  const shorter = a.length < b.length ? a : b
  const longer = a.length < b.length ? b : a
  if (longer.length === 0) return 1.0
  let matches = 0
  for (let i = 0; i < shorter.length; i++) {
    if (longer.includes(shorter[i])) matches++
  }
  return matches / shorter.length
}

export default function HistorySidebar({ open, onToggle }) {
  const [items, setItems] = useState(loadHistory)
  const [ctxMenu, setCtxMenu] = useState(null)
  const [renameId, setRenameId] = useState(null)
  const [renameVal, setRenameVal] = useState('')
  const [activeId, setActiveId] = useState(null)

  // 监听 storage 变化、自定义刷新事件、当前对话高亮
  useEffect(() => {
    const refresh = () => setItems(loadHistory())
    const onActive = (e) => setActiveId(e.detail?.id ?? null)
    window.addEventListener('storage', refresh)
    window.addEventListener('chat-history-changed', refresh)
    window.addEventListener('conv-active', onActive)
    return () => {
      window.removeEventListener('storage', refresh)
      window.removeEventListener('chat-history-changed', refresh)
      window.removeEventListener('conv-active', onActive)
    }
  }, [])

  const handleContextMenu = (e, item) => {
    e.preventDefault()
    e.stopPropagation()
    setCtxMenu({ x: e.clientX, y: e.clientY, item })
  }

  const closeMenu = () => setCtxMenu(null)

  const doRename = () => {
    if (!renameVal.trim()) { setRenameId(null); return }
    const history = loadHistory().map(i => i.id === renameId ? { ...i, question: renameVal.trim() } : i)
    localStorage.setItem('chat_history', JSON.stringify(history))
    setItems(history)
    setRenameId(null)
    closeMenu()
  }

  const doDelete = (id) => {
    const history = loadHistory().filter(i => i.id !== id)
    localStorage.setItem('chat_history', JSON.stringify(history))
    setItems(history)
    // 同时清除对应消息记录
    try { localStorage.removeItem('chat_messages_' + id) } catch {}
    closeMenu()
  }

  const doPin = (id) => {
    const history = loadHistory()
    const idx = history.findIndex(i => i.id === id)
    if (idx < 0) return
    const [item] = history.splice(idx, 1)
    history.unshift({ ...item, pinned: true })
    localStorage.setItem('chat_history', JSON.stringify(history))
    setItems(history)
    closeMenu()
  }

  const restoreConversation = (item) => {
    window.dispatchEvent(new CustomEvent('restore-loading', { detail: { loading: true } }))
    setTimeout(() => {
      try {
        const saved = localStorage.getItem('chat_messages')
        if (saved) {
          const msgs = JSON.parse(saved)
          if (msgs && msgs.length > 0) {
            window.dispatchEvent(new CustomEvent('restore-messages', { detail: { messages: msgs } }))
          }
        }
      } catch {}
      window.dispatchEvent(new CustomEvent('restore-conversation', {
        detail: { id: item.id, question: item.question, time: item.time },
      }))
      window.dispatchEvent(new CustomEvent('restore-loading', { detail: { loading: false } }))
    }, 300)
  }

  return (
    <div style={{ position: 'relative', display: 'flex' }} onClick={closeMenu}>
      <button onClick={onToggle}
        style={{
          position: 'fixed', top: 60, left: open ? 220 : 0, zIndex: 999,
          width: 28, height: 28, borderRadius: '0 4px 4px 0',
          border: '1px solid var(--border-color)', borderLeft: 'none',
          background: 'var(--bg-primary)', color: 'var(--text-muted)',
          cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center',
          transition: 'left 0.3s ease',
        }}
      >
        {open ? '◀' : '▶'}
      </button>

      <motion.aside
        animate={{ width: open ? 220 : 0 }}
        transition={{ duration: 0.3 }}
        style={{ background: 'var(--bg-secondary)', borderRight: '1px solid var(--border-color)', overflow: 'hidden', flexShrink: 0 }}
      >
        <div style={{ width: 220, padding: '48px 12px 12px' }}>
          <h3 style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>💬 历史对话</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {items.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '8px 0', textAlign: 'center' }}>暂无历史对话</div>
            ) : (
              [...items].reverse().map(h => (
                <div key={h.id}>
                  {renameId === h.id ? (
                    <div style={{ display: 'flex', gap: 4 }}>
                      <input value={renameVal} onChange={e => setRenameVal(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') doRename(); if (e.key === 'Escape') setRenameId(null) }}
                        autoFocus
                        style={{ flex: 1, padding: '6px 8px', fontSize: 12, borderRadius: 6, background: 'var(--bg-input)', border: '1px solid var(--accent)', color: 'var(--text-primary)', outline: 'none' }} />
                      <button onClick={doRename} style={{ padding: '4px 8px', fontSize: 11, borderRadius: 4, border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer' }}>✓</button>
                    </div>
                  ) : (
                    <div
                      onClick={() => restoreConversation(h)}
                      onContextMenu={(e) => handleContextMenu(e, h)}
                      style={{
                        padding: '8px 10px', borderRadius: 8, cursor: 'pointer',
                        background: activeId === h.id ? 'rgba(52,152,219,0.12)' : h.pinned ? 'rgba(138,155,174,0.1)' : 'var(--bg-card)',
                        border: activeId === h.id ? '1px solid #3498db' : h.pinned ? '1px solid var(--accent)' : '1px solid var(--border-color)',
                        fontSize: 13, color: 'var(--text-primary)', transition: 'all 0.2s',
                      }}
                    >
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {h.pinned ? '📌 ' : ''}{h.question || h.preview || '历史对话'}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{h.time || ''}</div>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </motion.aside>

      {/* 右键菜单 */}
      {ctxMenu && (
        <div style={{
          position: 'fixed', top: ctxMenu.y, left: ctxMenu.x, zIndex: 9999,
          background: 'var(--bg-card)', border: '1px solid var(--border-color)',
          borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
          padding: '4px 0', minWidth: 120,
        }}>
          {[
            { label: '✏️ 重命名', action: () => { setRenameId(ctxMenu.item.id); setRenameVal(ctxMenu.item.question || ctxMenu.item.preview); } },
            { label: '📌 置顶', action: () => doPin(ctxMenu.item.id) },
            { label: '🗑️ 删除', action: () => doDelete(ctxMenu.item.id), danger: true },
          ].map(opt => (
            <div key={opt.label}
              onClick={opt.action}
              style={{
                padding: '6px 14px', fontSize: 12, cursor: 'pointer',
                color: opt.danger ? '#e74c3c' : 'var(--text-primary)',
              }}
              onMouseEnter={e => e.target.style.background = 'var(--bg-hover)'}
              onMouseLeave={e => e.target.style.background = 'transparent'}
            >{opt.label}</div>
          ))}
        </div>
      )}
    </div>
  )
}
