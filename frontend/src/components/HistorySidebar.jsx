import { motion } from 'framer-motion'
import { useState, useEffect, useCallback } from 'react'

function loadHistory() {
  try { return JSON.parse(localStorage.getItem('chat_history') || '[]') }
  catch { return [] }
}

function saveHistory(items) {
  localStorage.setItem('chat_history', JSON.stringify(items))
}

/** 按日期分组（排除置顶项） */
function groupByDate(items) {
  const now = new Date()
  const today = now.toDateString()
  const yesterday = new Date(now - 86400000).toDateString()
  const groups = { '今天': [], '昨天': [], '更早': [] }
  items.forEach(item => {
    try {
      const d = new Date(item.time)
      const ds = d.toDateString()
      if (ds === today) groups['今天'].push(item)
      else if (ds === yesterday) groups['昨天'].push(item)
      else groups['更早'].push(item)
    } catch { groups['更早'].push(item) }
  })
  return Object.entries(groups).filter(([_, v]) => v.length > 0)
}

/** 用蓝色高亮替代原有浅灰 */
const activeCardStyle = {
  background: '#e3f2fd',
  border: '1px solid #1976d2',
  borderLeft: '3px solid #1976d2',
}

const defaultCardStyle = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
}

const pinnedCardStyle = {
  background: 'rgba(138,155,174,0.1)',
  border: '1px solid var(--accent)',
}

export default function HistorySidebar({ open, onToggle }) {
  const [items, setItems] = useState(loadHistory)
  const [ctxMenu, setCtxMenu] = useState(null)   // { x, y, item }
  const [renameId, setRenameId] = useState(null)
  const [renameVal, setRenameVal] = useState('')
  const [activeId, setActiveId] = useState(null)
  const [toast, setToast] = useState(null) // { message, type } 顶部提示

  // 刷新列表 & 同步活跃对话 ID
  useEffect(() => {
    const refresh = () => setItems(loadHistory())
    const onActive = (e) => setActiveId(e.detail?.id ?? null)
    const onToast = (e) => {
      setToast(e.detail)
      setTimeout(() => setToast(null), 2200)
    }
    window.addEventListener('storage', refresh)
    window.addEventListener('chat-history-changed', refresh)
    window.addEventListener('conv-active', onActive)
    window.addEventListener('toast', onToast)
    return () => {
      window.removeEventListener('storage', refresh)
      window.removeEventListener('chat-history-changed', refresh)
      window.removeEventListener('conv-active', onActive)
      window.removeEventListener('toast', onToast)
    }
  }, [])

  const handleContextMenu = useCallback((e, item) => {
    e.preventDefault(); e.stopPropagation()
    setCtxMenu({ x: e.clientX, y: e.clientY, item })
  }, [])

  const closeMenu = useCallback(() => setCtxMenu(null), [])

  const doRename = useCallback(() => {
    if (!renameVal.trim()) { setRenameId(null); return }
    const history = loadHistory().map(i => i.id === renameId ? { ...i, question: renameVal.trim() } : i)
    saveHistory(history)
    setItems(history); setRenameId(null); closeMenu()
  }, [renameId, renameVal, closeMenu])

  const doDelete = useCallback((id) => {
    fetch('/api/conversation/end', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: String(id) }),
    }).catch(() => {})
    try { localStorage.removeItem('chat_messages_' + id) } catch {}
    const history = loadHistory().filter(i => i.id !== id)
    saveHistory(history)
    setItems(history); closeMenu()
  }, [closeMenu])

  // =====================================================================
  //  置顶 / 取消置顶
  // =====================================================================

  const doPin = useCallback((id) => {
    const history = loadHistory()
    // 检查当前置顶数量
    const pinnedCount = history.filter(i => i.pinned).length
    if (pinnedCount >= 3) {
      // 用临时消息提示（非阻塞）
      window.dispatchEvent(new CustomEvent('toast', { detail: { message: '最多置顶 3 个对话', type: 'warning' } }))
      closeMenu()
      return
    }
    const newHistory = history.map(i =>
      i.id === id ? { ...i, pinned: true, pinnedAt: Date.now() } : i
    )
    saveHistory(newHistory)
    setItems(newHistory); closeMenu()
  }, [closeMenu])

  const doUnpin = useCallback((id) => {
    const history = loadHistory().map(i =>
      i.id === id ? { ...i, pinned: false, pinnedAt: undefined } : i
    )
    saveHistory(history)
    setItems(history); closeMenu()
  }, [closeMenu])

  // =====================================================================
  //  恢复对话
  // =====================================================================

  const restoreConversation = useCallback((item) => {
    if (activeId && activeId !== item.id) {
      fetch('/api/conversation/end', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conv_id: String(activeId) }),
      }).catch(() => {})
    }
    setActiveId(item.id)
    window.dispatchEvent(new CustomEvent('conv-active', { detail: { id: item.id } }))
    window.dispatchEvent(new CustomEvent('restore-loading', { detail: { loading: true } }))
    setTimeout(() => {
      let msgs = []
      try {
        const key = 'chat_messages_' + item.id
        let saved = localStorage.getItem(key)
        if (!saved) saved = localStorage.getItem('chat_messages')
        if (saved) msgs = JSON.parse(saved)
      } catch {}
      // 统一通过 switch-conversation 事件切换对话（替代旧的 restore-messages + restore-conversation）
      window.dispatchEvent(new CustomEvent('switch-conversation', {
        detail: { convId: item.id, question: item.question || '', messages: msgs },
      }))
      window.dispatchEvent(new CustomEvent('restore-loading', { detail: { loading: false } }))
    }, 300)
  }, [activeId])

  // =====================================================================
  //  分组逻辑：置顶 → 按 pinnedAt 倒序，其余按日期
  // =====================================================================

  const pinnedItems = items
    .filter(i => i.pinned)
    .sort((a, b) => (b.pinnedAt || 0) - (a.pinnedAt || 0))

  const unpinnedItems = items.filter(i => !i.pinned)
  const dateGroups = groupByDate([...unpinnedItems].reverse())

  // 判断当前卡片是否为活跃对话
  const isActive = (item) => activeId === item.id || activeId === item.id

  return (
    <div style={{ position: 'relative', display: 'flex' }} onClick={closeMenu}>
      <button onClick={onToggle}
        style={{ position: 'fixed', top: 60, left: open ? 220 : 0, zIndex: 999, width: 28, height: 28, borderRadius: '0 4px 4px 0', border: '1px solid var(--border-color)', borderLeft: 'none', background: 'var(--bg-primary)', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'left 0.3s ease' }}>
        {open ? '◀' : '▶'}
      </button>

      <motion.aside animate={{ width: open ? 220 : 0 }} transition={{ duration: 0.3 }}
        style={{ background: 'var(--bg-secondary)', borderRight: '1px solid var(--border-color)', overflow: 'hidden', flexShrink: 0, position: 'relative' }}>
        {/* Toast 提示 */}
        {toast && (
          <div style={{
            position: 'absolute', top: 8, left: 12, right: 12, zIndex: 100,
            padding: '6px 10px', borderRadius: 6, fontSize: 12,
            background: toast.type === 'warning' ? '#fff3e0' : '#e8f5e9',
            color: toast.type === 'warning' ? '#e65100' : '#2e7d32',
            border: `1px solid ${toast.type === 'warning' ? '#ffcc02' : '#66bb6a'}`,
            textAlign: 'center',
          }}>
            {toast.message}
          </div>
        )}
        <div style={{ width: 220, padding: '48px 12px 12px' }}>
          <h3 style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>💬 历史对话</h3>
          {items.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '8px 0', textAlign: 'center' }}>暂无历史对话</div>
          ) : (
            <>
              {/* 置顶分组 */}
              {pinnedItems.length > 0 && (
                <div key="pinned-group" style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, paddingLeft: 2 }}>
                    📌 置顶（{pinnedItems.length}/3）
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    {pinnedItems.map(h => (
                      <div key={h.id}>
                        {renameId === h.id ? (
                          <div style={{ display: 'flex', gap: 4 }}>
                            <input value={renameVal} onChange={e => setRenameVal(e.target.value)}
                              onKeyDown={e => { if (e.key === 'Enter') doRename(); if (e.key === 'Escape') setRenameId(null) }} autoFocus
                              style={{ flex: 1, padding: '6px 8px', fontSize: 12, borderRadius: 6, background: 'var(--bg-input)', border: '1px solid var(--accent)', color: 'var(--text-primary)', outline: 'none' }} />
                            <button onClick={doRename} style={{ padding: '4px 8px', fontSize: 11, borderRadius: 4, border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer' }}>✓</button>
                          </div>
                        ) : (
                          <div onClick={() => restoreConversation(h)} onContextMenu={(e) => handleContextMenu(e, h)}
                            style={{
                              padding: '7px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 13, color: 'var(--text-primary)',
                              ...(isActive(h) ? activeCardStyle : pinnedCardStyle),
                              transition: 'all 0.15s',
                            }}>
                            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>📌 {h.question || h.preview || '历史对话'}</div>
                            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{h.time || ''}</div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 日期分组 */}
              {dateGroups.map(([groupName, groupItems]) => (
                <div key={groupName} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, paddingLeft: 2 }}>{groupName}</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    {groupItems.map(h => (
                      <div key={h.id}>
                        {renameId === h.id ? (
                          <div style={{ display: 'flex', gap: 4 }}>
                            <input value={renameVal} onChange={e => setRenameVal(e.target.value)}
                              onKeyDown={e => { if (e.key === 'Enter') doRename(); if (e.key === 'Escape') setRenameId(null) }} autoFocus
                              style={{ flex: 1, padding: '6px 8px', fontSize: 12, borderRadius: 6, background: 'var(--bg-input)', border: '1px solid var(--accent)', color: 'var(--text-primary)', outline: 'none' }} />
                            <button onClick={doRename} style={{ padding: '4px 8px', fontSize: 11, borderRadius: 4, border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer' }}>✓</button>
                          </div>
                        ) : (
                          <div onClick={() => restoreConversation(h)} onContextMenu={(e) => handleContextMenu(e, h)}
                            style={{
                              padding: '7px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 13, color: 'var(--text-primary)',
                              ...(isActive(h) ? activeCardStyle : defaultCardStyle),
                              transition: 'all 0.15s',
                            }}>
                            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.question || h.preview || '历史对话'}</div>
                            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{h.time || ''}</div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </motion.aside>

      {/* 右键菜单 */}
      {ctxMenu && (
        <div style={{ position: 'fixed', top: ctxMenu.y, left: ctxMenu.x, zIndex: 9999, background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.15)', padding: '4px 0', minWidth: 120 }}
          onClick={e => e.stopPropagation()}>
          {[
            { label: '✏️ 重命名', action: () => { setRenameId(ctxMenu.item.id); setRenameVal(ctxMenu.item.question || ctxMenu.item.preview) } },
            { label: ctxMenu.item.pinned ? '📌 取消置顶' : '📌 置顶', action: () => ctxMenu.item.pinned ? doUnpin(ctxMenu.item.id) : doPin(ctxMenu.item.id) },
            { label: '🗑️ 删除', action: () => doDelete(ctxMenu.item.id), danger: true },
          ].map(opt => (
            <div key={opt.label} onClick={opt.action}
              style={{ padding: '6px 14px', fontSize: 12, cursor: 'pointer', color: opt.danger ? '#e74c3c' : 'var(--text-primary)' }}
              onMouseEnter={e => e.target.style.background = 'var(--bg-hover)'}
              onMouseLeave={e => e.target.style.background = 'transparent'}>{opt.label}</div>
          ))}
        </div>
      )}
    </div>
  )
}
