import { motion } from 'framer-motion'
import { useState, useEffect } from 'react'

// 简易加密存储（浏览器环境，非高安全需求）
function encrypt(text) {
  return btoa(encodeURIComponent(text))
}
function decrypt(encoded) {
  try { return decodeURIComponent(atob(encoded)) } catch { return '' }
}

const STORAGE_PWD_KEY = 'db_remember_pwd'

export default function ToolsSidebar({ open, onToggle }) {
  const [dbStatus, setDbStatus] = useState('检测中')
  const [cacheStatus, setCacheStatus] = useState('检测中')
  const [db, setDb] = useState({
    host: 'host.docker.internal', port: '3306', database: '', user: 'root', password: '',
  })
  const [statusMsg, setStatusMsg] = useState('')
  const [rememberMe, setRememberMe] = useState(false)
  const [dbType, setDbType] = useState('mysql')

  // 恢复记住的密码
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_PWD_KEY)
      if (saved) {
        setDb(prev => ({ ...prev, password: decrypt(saved) }))
        setRememberMe(true)
      }
    } catch {}
  }, [])

  useEffect(() => {
    fetch('/api/cache/stats')
      .then(r => r.json())
      .then(d => setCacheStatus(`运行中 ${d.hit_rate || 0}%`))
      .catch(() => setCacheStatus('未连接'))
    fetch('/api/db/status')
      .then(r => r.json())
      .then(d => {
        if (d.connected) {
          setDbStatus(`已连接，共 ${d.active_tables || 0} 个表`)
          if (d.db_type) setDbType(d.db_type)
          if (d.host) setDb(prev => ({ ...prev, host: d.host }))
          if (d.port) setDb(prev => ({ ...prev, port: d.port }))
          if (d.database) setDb(prev => ({ ...prev, database: d.database }))
        } else {
          setDbStatus('未连接')
        }
      })
      .catch(() => setDbStatus('未连接'))
  }, [])

  const showAdvanced = db.database.trim().length > 0

  const testConnection = async () => {
    setStatusMsg('测试中...')
    try {
      const r = await fetch('/api/db/test-connection', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ db_type: dbType, ...db }),
      })
      const d = await r.json()
      setStatusMsg(d.success ? `✅ 连接成功 (${d.latency_ms}ms)` : `❌ ${d.message || '失败'}`)
    } catch (e) {
      setStatusMsg('❌ 请求失败')
    }
  }

  const connectDb = async () => {
    setStatusMsg('连接中...')
    try {
      const r = await fetch('/api/db/connect', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ db_type: dbType, ...db }),
      })
      const d = await r.json()
      if (d.success) {
        const tableInfo = d.tables?.length ? `，共 ${d.tables.length} 个表` : ''
        setStatusMsg(`✅ 已连接到 ${d.db_type}${tableInfo}`)
        setDbStatus(`已连接${tableInfo}`)
        // 记住密码
        if (rememberMe) {
          localStorage.setItem(STORAGE_PWD_KEY, encrypt(db.password))
        } else {
          localStorage.removeItem(STORAGE_PWD_KEY)
        }
      } else {
        setStatusMsg(`❌ ${d.message || '连接失败'}`)
      }
    } catch (e) {
      setStatusMsg('❌ 连接请求失败')
    }
  }

  return (
    <div style={{ position: 'relative', display: 'flex' }}>
      <button onClick={onToggle} style={{
        position: 'fixed', top: 60, right: open ? 280 : 0, zIndex: 999,
        width: 28, height: 28, borderRadius: '4px 0 0 4px',
        border: '1px solid var(--border-color)', borderRight: 'none',
        background: 'var(--bg-primary)', color: 'var(--text-muted)',
        cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'right 0.3s ease',
      }}>{open ? '▶' : '◀'}</button>

      <motion.aside animate={{ width: open ? 280 : 0 }} transition={{ duration: 0.3 }} style={{
        background: 'var(--bg-secondary)', borderLeft: '1px solid var(--border-color)',
        overflow: 'hidden', flexShrink: 0,
      }}>
        <div style={{ width: 280, padding: '48px 16px 16px' }}>
          <Section title="📁 数据导入">
            <div style={{ border: '2px dashed var(--border-color)', borderRadius: 12, padding: 24, textAlign: 'center', cursor: 'pointer', color: 'var(--text-muted)', fontSize: 12 }}>
              📄 点击或拖拽上传<br />CSV / Excel / PDF
            </div>
          </Section>

          <Section title="🗄️ 数据库连接">
            <Label>数据库类型</Label>
            <select value={dbType} onChange={e => setDbType(e.target.value)}
              style={{ width: '100%', padding: '6px 10px', fontSize: 12, background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 8, color: 'var(--text-primary)', outline: 'none' }}>
              <option value="mysql">MySQL</option>
              <option value="postgres">PostgreSQL</option>
              <option value="sqlite">SQLite</option>
            </select>
            <Label>主机地址</Label>
            <Input value={db.host} onChange={e => setDb({...db, host: e.target.value})} />
            <Label>端口</Label>
            <Input value={db.port} type="number" onChange={e => setDb({...db, port: e.target.value})} />
            <Label>数据库名</Label>
            <Input value={db.database} onChange={e => setDb({...db, database: e.target.value})} placeholder="输入数据库名" />

            {/* 数据库名非空时才显示用户名/密码 */}
            {showAdvanced && (
              <>
                <Label>用户名</Label>
                <Input value={db.user} onChange={e => setDb({...db, user: e.target.value})} />
                <Label>密码</Label>
                <Input value={db.password} type="password" onChange={e => setDb({...db, password: e.target.value})} />
                <label style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 6, fontSize: 11, color: 'var(--text-muted)', cursor: 'pointer' }}>
                  <input type="checkbox" checked={rememberMe} onChange={e => setRememberMe(e.target.checked)} />
                  记住密码
                </label>
              </>
            )}

            <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
              <button onClick={testConnection} style={btnStyle}>🔌 测试连接</button>
              <button onClick={connectDb} style={{ ...btnStyle, background: 'var(--accent)', color: '#fff', border: 'none' }}>🔗 连接</button>
            </div>
            {statusMsg && <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-muted)', padding: 6, borderRadius: 6, background: 'var(--bg-input)' }}>{statusMsg}</div>}
          </Section>

          <Section title="🖥️ 系统状态">
            <StatusRow label="缓存服务" value={cacheStatus} />
            <StatusRow label="数据库" value={dbStatus} />
          </Section>
        </div>
      </motion.aside>
    </div>
  )
}

function Section({ title, children }) { return <div style={{ marginBottom: 20 }}><h3 style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 8 }}>{title}</h3>{children}</div> }
function Label({ children }) { return <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginTop: 8, marginBottom: 2 }}>{children}</label> }
function Input(props) { return <input {...props} style={{ width: '100%', padding: '6px 10px', fontSize: 12, background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 8, color: 'var(--text-primary)', outline: 'none' }} /> }
function StatusRow({ label, value }) {
  const dot = value.includes('运行') || value.includes('已连接') ? '#7EB87E' : 'var(--text-muted)'
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0' }}>
      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span style={{ color: 'var(--text-muted)' }}>
        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: dot, marginRight: 4 }}></span>{value}
      </span>
    </div>
  )
}
const btnStyle = { flex: 1, padding: '6px', fontSize: 12, background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)', borderRadius: 8, cursor: 'pointer' }
