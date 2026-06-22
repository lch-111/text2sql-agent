import { useState, useEffect, useCallback, useRef } from 'react'
import { motion } from 'framer-motion'
import ReactEChartsCore from 'echarts-for-react'
import * as echarts from 'echarts'
import { DndContext, closestCenter } from '@dnd-kit/core'
import { SortableContext, useSortable, arrayMove, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Resizable } from 'react-resizable'

/* ============================================================================
   全局变量（var 避免 TDZ）
   ============================================================================ */
var panelVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.3 } },
}

function Label({ children }) {
  return <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 2 }}>{children}</label>
}

var selectStyle = {
  padding: '4px 8px', fontSize: 12, borderRadius: 6,
  background: 'var(--bg-input)', border: '1px solid var(--border-color)',
  color: 'var(--text-primary)', outline: 'none',
}
var smallInputStyle = {
  padding: '4px 8px', fontSize: 12, borderRadius: 6,
  background: 'var(--bg-input)', border: '1px solid var(--border-color)',
  color: 'var(--text-primary)', outline: 'none', width: 120,
}

// 21 种配色方案（名称简洁）
var COLOR_SCHEMES = [
  { name: '莫兰迪', colors: ['#8A9BAE','#B8C5D0','#D4A574','#A3B5A5','#C4A882','#9B8EA8','#D0B8A0','#A8B8C8'] },
  { name: '蓝调', colors: ['#5470C6','#91CC75','#FAC858','#EE6666','#73C0DE','#3BA272','#FC8452','#9A60B4'] },
  { name: '马卡龙', colors: ['#FFD1DC','#B5EAD7','#C7CEEA','#FFDAC1','#E2F0CB','#F0E68C','#DDA0DD','#87CEEB'] },
  { name: '霓虹', colors: ['#FF006E','#FB5607','#FFBE0B','#8338EC','#3A86FF','#00F5D4','#F15BB5','#9B5DE5'] },
  { name: '大地', colors: ['#8B5E3C','#A67C52','#C49A6C','#D4B896','#E8D5B7','#6B8E6B','#8FBC8F','#C4A882'] },
  { name: '海洋', colors: ['#0077B6','#00B4D8','#90E0EF','#CAF0F8','#023E8A','#48CAE4','#ADE8F4','#03045E'] },
  { name: '日落', colors: ['#FF6B35','#F7C59F','#EFEFD0','#004E89','#1A659E','#FF9F1C','#E71D36','#2EC4B6'] },
  { name: '森林', colors: ['#2D6A4F','#40916C','#52B788','#95D5B2','#1B4332','#74C69D','#D8F3DC','#52796F'] },
  { name: '樱花', colors: ['#FFB7C5','#FF8C9E','#FF6B7F','#E85D75','#D4A0B0','#F4CED8','#FADADD','#C9A0B0'] },
  { name: '极光', colors: ['#00F260','#0575E6','#7F00FF','#E100FF','#00C9FF','#92FE9D','#F53844','#42378F'] },
  { name: '复古', colors: ['#D4A373','#FAEDCD','#CCD5AE','#E9EDC9','#A3B18A','#588157','#4A4E69','#9A8C98'] },
  { name: '糖果', colors: ['#FF6B6B','#FFE66D','#4ECDC4','#95E1D3','#F38181','#AA96DA','#FCBAD3','#A8D8EA'] },
  { name: '金属', colors: ['#A8A8A8','#C0C0C0','#D4AF37','#B8860B','#8B8682','#E8E8E8','#696969','#F5F5DC'] },
  { name: '星空', colors: ['#0B3D91','#1B4F72','#2E86C1','#3498DB','#5DADE2','#85C1E9','#AED6F1','#D6EAF8'] },
  { name: '暖阳', colors: ['#FF9F43','#FECA57','#FF6348','#EE5A24','#F8A5C2','#F3A683','#F7D794','#F5CD79'] },
  { name: '薄荷', colors: ['#00B894','#00CEC9','#55EFC4','#81ECEC','#00A8CC','#0ABDE3','#A29BFE','#6C5CE7'] },
  { name: '浆果', colors: ['#6C3483','#8E44AD','#BB8FCE','#D2B4DE','#E8DAEF','#7D3C98','#A569BD','#C39BD3'] },
  { name: '沙漠', colors: ['#E67E22','#D35400','#F39C12','#F1C40F','#E59866','#DC7633','#F0B27A','#FAD7A0'] },
  { name: '冰川', colors: ['#85C1E9','#5DADE2','#3498DB','#2E86C1','#AED6F1','#D6EAF8','#EBF5FB','#7FB3D8'] },
  { name: '秋叶', colors: ['#C0392B','#E74C3C','#D35400','#E67E22','#F39C12','#A04000','#BA4A00','#DC7633'] },
  { name: '紫罗兰', colors: ['#4A235A','#6C3483','#7D3C98','#A569BD','#BB8FCE','#512E5F','#8E44AD','#9B59B6'] },
]

// -----------------------------------------------------------------------------
// 图表配置生成函数
// -----------------------------------------------------------------------------
function makeChartOption(item, effectiveColor) {
  const d = item.chartData
  if (!d || !d.labels) return null
  const colors = COLOR_SCHEMES[effectiveColor]?.colors || COLOR_SCHEMES[0].colors
  const ct = item.chartType || 'bar'
  const labels = d.labels
  const values = d.values

  const base = {
    tooltip: { trigger: ct === 'pie' ? 'item' : 'axis' },
    grid: { left: 55, right: 25, top: 25, bottom: 55 },
    color: colors,
  }

  if (ct === 'pie') return { ...base, series: [{ type: 'pie', data: labels.map((l, i) => ({ name: l, value: values[i] })) }] }
  if (ct === 'scatter' || ct === 'effectScatter') return { ...base, xAxis: { type: 'value' }, yAxis: { type: 'value' }, series: [{ type: ct, data: labels.map((l, i) => [Number(l) || 0, Number(values[i]) || 0]) }] }
  if (ct === 'funnel') return { ...base, series: [{ type: 'funnel', data: labels.map((l, i) => ({ name: l, value: values[i] })) }] }
  if (ct === 'gauge') return { ...base, series: [{ type: 'gauge', detail: { formatter: '{value}' }, data: [{ value: values[0] || 0, name: '' }] }] }

  return {
    ...base,
    xAxis: { type: 'category', data: labels, axisLabel: { color: '#8892a8' } },
    yAxis: { type: 'value', axisLabel: { color: '#8892a8' } },
    series: [{ type: ct === 'line' ? 'line' : 'bar', data: values, smooth: ct === 'line' }],
  }
}

// -----------------------------------------------------------------------------
// 可拖拽/缩放/下载的图表卡片组件（单图样式预览 → 确定/取消）
// -----------------------------------------------------------------------------
function SortableChartCard({ item, onRemove, onDownload, onResize, effectiveColor, onUpdateColor }) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: item.id })
  const [menuOpen, setMenuOpen] = useState(false)
  const [stylePanelOpen, setStylePanelOpen] = useState(false)
  const [singlePreviewColor, setSinglePreviewColor] = useState(null) // 单图预览颜色索引
  const chartRef = useRef(null)

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    background: 'var(--bg-card)',
    borderRadius: 10,
    overflow: 'hidden',
    position: 'relative',
    boxShadow: 'var(--shadow)',
    border: '1px solid var(--border-color)',
    display: 'flex',
    flexDirection: 'column',
  }

  // 图表实际使用的颜色索引：预览 > 已保存 > 默认
  const activeColor = singlePreviewColor !== null ? singlePreviewColor : effectiveColor
  const opt = makeChartOption(item, activeColor)

  const handleDownload = () => {
    if (chartRef.current) {
      const instance = chartRef.current.getEchartsInstance()
      if (instance) {
        const url = instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
        const a = document.createElement('a')
        a.href = url; a.download = `chart_${item.title || item.id}.png`; a.click()
      }
    }
  }

  const handleStyleSelect = (index) => {
    // 点击配色方案仅预览，不保存
    setSinglePreviewColor(index)
  }

  const confirmStyle = () => {
    if (singlePreviewColor !== null) {
      onUpdateColor(item.id, singlePreviewColor)
    }
    setSinglePreviewColor(null)
    setStylePanelOpen(false)
    setMenuOpen(false)
  }

  const cancelStyle = () => {
    setSinglePreviewColor(null)
    setStylePanelOpen(false)
    setMenuOpen(false)
  }

  return (
    <Resizable
      height={item.height || 300}
      width={Infinity}
      onResize={(e, { size }) => onResize(item.id, size.height)}
      axis="y"
      resizeHandles={['s']}
      minConstraints={[0, 200]}
      maxConstraints={[0, 600]}
    >
      <div ref={setNodeRef} style={style} {...attributes}>
        {/* 拖拽手柄（仅标题） */}
        <div className="drag-handle" {...listeners} style={{
          cursor: 'grab',
          padding: '8px 12px',
          fontSize: 12,
          color: 'var(--text-secondary)',
          borderBottom: '1px solid var(--border-color)',
          userSelect: 'none',
          flexShrink: 0,
        }}>
          <span style={{ fontWeight: 500 }}>{item.title}</span>
        </div>

        {/* 操作按钮独立一行 */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '4px 12px', background: 'var(--bg-card)' }}>
          <div style={{ position: 'relative' }}>
            <button onClick={() => setMenuOpen(!menuOpen)} style={{
              fontSize: 14, cursor: 'pointer', background: 'transparent',
              border: 'none', color: 'var(--text-muted)', padding: '2px 6px'
            }}>:</button>
            {menuOpen && (
              <div style={{
                position: 'absolute', right: 0, top: '100%', zIndex: 10,
                background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                borderRadius: 6, padding: '4px 0', minWidth: 100,
                boxShadow: '0 4px 12px rgba(0,0,0,0.1)'
              }}>
                <div onClick={() => { setMenuOpen(false); handleDownload() }} style={{ padding: '4px 12px', cursor: 'pointer', fontSize: 12, color: 'var(--text-primary)' }}>下载</div>
                <div onClick={() => { setMenuOpen(false); onRemove(item.id) }} style={{ padding: '4px 12px', cursor: 'pointer', fontSize: 12, color: '#e74c3c' }}>删除</div>
                <div onClick={() => { setMenuOpen(false); setStylePanelOpen(true); setSinglePreviewColor(null) }} style={{ padding: '4px 12px', cursor: 'pointer', fontSize: 12, color: 'var(--text-primary)' }}>修改样式</div>
              </div>
            )}
          </div>
        </div>

        {/* 单个图表样式修改面板（预览-确定-取消） */}
        {stylePanelOpen && (
          <div style={{ padding: '8px 12px', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-color)' }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>选择配色方案</div>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
              {COLOR_SCHEMES.map((cs, idx) => (
                <div
                  key={idx}
                  onClick={() => handleStyleSelect(idx)}
                  style={{
                    cursor: 'pointer',
                    padding: '2px 8px',
                    borderRadius: 4,
                    fontSize: 11,
                    border: activeColor === idx ? '2px solid var(--accent)' : '1px solid var(--border-color)',
                    background: activeColor === idx ? 'rgba(138,155,174,0.1)' : 'transparent',
                    color: 'var(--text-primary)'
                  }}
                >
                  {cs.name}
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={confirmStyle} style={{ fontSize: 11, cursor: 'pointer', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 4, padding: '2px 8px' }}>确定</button>
              <button onClick={cancelStyle} style={{ fontSize: 11, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 4, padding: '2px 8px', color: 'var(--text-muted)' }}>取消</button>
            </div>
          </div>
        )}

        {/* 图表区域 */}
        <div style={{ flex: 1, minHeight: 0 }}>
          {opt ? (
            <ReactEChartsCore ref={chartRef} option={opt} style={{ width: '100%', height: item.height || 300 }} />
          ) : (
            <div style={{ height: item.height || 300, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>暂无数据</div>
          )}
        </div>
      </div>
    </Resizable>
  )
}

// -----------------------------------------------------------------------------
// 数据大屏面板
// -----------------------------------------------------------------------------
function DashboardPanel() {
  const DASHBOARDS_KEY = 'dashboard_manager'
  const dashboardRef = useRef(null)
  const headerRef = useRef(null)

  const [dashboards, setDashboards] = useState(() => {
    try { return JSON.parse(localStorage.getItem(DASHBOARDS_KEY) || 'null') || [{ id: 'default', name: '数据看板 1', items: [] }] }
    catch { return [{ id: 'default', name: '数据看板 1', items: [] }] }
  })
  const [activeIdx, setActiveIdx] = useState(0)
  const active = dashboards[activeIdx] || dashboards[0]
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameVal, setRenameVal] = useState('')
  const [items, setItems] = useState(active.items || [])
  const [previewColor, setPreviewColor] = useState(null) // 全局预览颜色

  const syncDashboards = (newItems) => {
    setDashboards(prev => {
      const next = [...prev]
      next[activeIdx] = { ...next[activeIdx], items: newItems }
      localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
      return next
    })
  }

  useEffect(() => {
    const d = dashboards[activeIdx] || dashboards[0]
    setItems(d.items || [])
    setPreviewColor(null)
  }, [activeIdx])

  const [builderVisible, setBuilderVisible] = useState(false)
  const [metricVisible, setMetricVisible] = useState(false)
  const [tables, setTables] = useState([])
  const [columns, setColumns] = useState([])
  const [builderForm, setBuilderForm] = useState({ table: '', xCol: '', yCol: '', chartType: 'bar', title: '' })
  const [metricForm, setMetricForm] = useState({ table: '', column: '', aggFunc: 'COUNT', filter: '', title: '' })

  useEffect(() => {
    fetch('/api/db/status').then(r => r.json()).then(d => setTables(d.tables || [])).catch(() => {})
    try {
      const pending = localStorage.getItem('pending_dashboard_item')
      if (pending) {
        const item = JSON.parse(pending)
        localStorage.removeItem('pending_dashboard_item')
        if (item) {
          const id = 'pending_' + Date.now()
          const newItem = { ...item, id, type: item.type || 'chart' }
          const targetName = item.targetDashboard
          if (targetName) {
            setDashboards(prev => {
              const next = [...prev]
              const idx = next.findIndex(d => d.name === targetName)
              if (idx >= 0) {
                next[idx] = { ...next[idx], items: [...next[idx].items, newItem] }
                localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
              }
              return next
            })
          } else {
            const newItems = [...items, newItem]
            setItems(newItems)
            setTimeout(() => syncDashboards(newItems), 50)
          }
        }
      }
    } catch {}
  }, [])

  const addCustomChart = async () => {
    const { table, xCol, yCol, chartType, title } = builderForm
    if (!table || !xCol || !yCol) return
    try {
      const payload = { table, x_column: xCol, y_column: yCol, chart_type: chartType, limit: 100 }
      const r = await fetch('/api/dashboard/chart-data', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      const chartDataJson = await r.json()
      const id = 'builder_' + Date.now()
      const newItem = { id, title: title || `${table} - ${xCol} × ${yCol}`, chartData: chartDataJson, chartType, table, xCol, yCol, type: 'chart', height: 300 }
      setItems(prev => {
        const next = [...prev, newItem]
        syncDashboards(next)
        return next
      })
      setBuilderVisible(false)
      setBuilderForm({ table: '', xCol: '', yCol: '', chartType: 'bar', title: '' })
    } catch {}
  }

  const addMetric = async () => {
    const { table, column, aggFunc, filter, title } = metricForm
    if (!table) return
    try {
      let sql = `SELECT ${aggFunc}(${column || '*'}) as val FROM ${table}`
      if (filter) sql += ` WHERE ${filter}`
      const r = await fetch('/api/dashboard/chart-data', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ table, x_column: '1', y_column: 'val', chart_type: 'bar', limit: 1, custom_sql: sql }),
      })
      const d = await r.json()
      const id = 'metric_' + Date.now()
      const newItem = { id, title: title || `${aggFunc}(${column || '*'})`, metricValue: d.values?.[0] ?? '—', type: 'metric' }
      setItems(prev => {
        const next = [...prev, newItem]
        syncDashboards(next)
        return next
      })
      setMetricVisible(false)
      setMetricForm({ table: '', column: '', aggFunc: 'COUNT', filter: '', title: '' })
    } catch {}
  }

  const [confirmDeleteId, setConfirmDeleteId] = useState(null)
  const removeItem = (id) => setConfirmDeleteId(id)
  const doRemove = () => {
    if (!confirmDeleteId) return
    setItems(prev => {
      const next = prev.filter(c => c.id !== confirmDeleteId)
      syncDashboards(next)
      return next
    })
    setConfirmDeleteId(null)
  }

  const handleDragEnd = (event) => {
    const { active, over } = event
    if (active.id !== over?.id) {
      const metricItems = items.filter(i => i.type === 'metric')
      const chartItems = items.filter(i => i.type !== 'metric')
      const oldIndex = chartItems.findIndex(i => i.id === active.id)
      const newIndex = chartItems.findIndex(i => i.id === over.id)
      const newChartItems = arrayMove(chartItems, oldIndex, newIndex)
      const newItems = [...metricItems, ...newChartItems]
      setItems(newItems)
      syncDashboards(newItems)
    }
  }

  const handleResize = (id, newHeight) => {
    setItems(prev => {
      const next = prev.map(it => it.id === id ? { ...it, height: newHeight } : it)
      syncDashboards(next)
      return next
    })
  }

  const handleUpdateColor = (itemId, colorIndex) => {
    setItems(prev => {
      const next = prev.map(it => it.id === itemId ? { ...it, manualColor: colorIndex } : it)
      syncDashboards(next)
      return next
    })
  }

  const addDashboard = () => {
    setDashboards(prev => {
      const next = [...prev, { id: 'db_' + Date.now(), name: `数据看板 ${prev.length + 1}`, items: [] }]
      localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
      return next
    })
    setActiveIdx(dashboards.length)
  }
  const delDashboard = (idx) => {
    if (dashboards.length <= 1) return
    setDashboards(prev => {
      const next = prev.filter((_, i) => i !== idx)
      localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
      return next
    })
    if (activeIdx >= idx) setActiveIdx(Math.max(0, activeIdx - 1))
  }
  const doRename = () => {
    if (!renameVal.trim()) { setRenaming(false); return }
    setDashboards(prev => {
      const next = [...prev]
      next[activeIdx] = { ...next[activeIdx], name: renameVal.trim() }
      localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
      return next
    })
    setRenaming(false)
  }
  const toggleFullscreen = () => {
    if (!isFullscreen) { dashboardRef.current?.requestFullscreen?.(); setIsFullscreen(true) }
    else { document.exitFullscreen?.(); setIsFullscreen(false) }
  }
  const loadColumns = async (table) => {
    if (!table) return
    try { const r = await fetch(`/api/dashboard/table-schema?table=${encodeURIComponent(table)}`); const d = await r.json(); setColumns(d.columns || []) }
    catch { setColumns([]) }
  }

  const applyTheme = () => {
    if (previewColor === null) return
    setItems(prev => {
      const next = prev.map(it => ({ ...it, manualColor: previewColor }))
      syncDashboards(next)
      return next
    })
    setPreviewColor(null)
  }
  const cancelPreview = () => setPreviewColor(null)

  const metricItems = items.filter(i => i.type === 'metric')
  const chartItems = items.filter(i => i.type !== 'metric')

  return (
    <div ref={dashboardRef} style={{ height: '100%', overflow: 'auto', background: 'var(--bg-secondary)', display: 'flex', flexDirection: 'column' }}>
      {/* 头部控件 */}
      <div ref={headerRef} style={{ flexShrink: 0 }}>
        {/* 多屏标签栏 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '12px 16px 0', flexWrap: 'wrap' }}>
          {dashboards.map((db, i) => (
            <div key={db.id} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <button onClick={() => setActiveIdx(i)} style={{
                padding: '4px 10px', fontSize: 13, cursor: 'pointer', borderRadius: 6,
                border: activeIdx === i ? '2px solid var(--accent)' : '1px solid var(--border-color)',
                background: activeIdx === i ? 'rgba(138,155,174,0.15)' : 'var(--bg-input)',
                color: 'var(--text-primary)', fontWeight: activeIdx === i ? 600 : 400
              }}>{db.name}</button>
              {dashboards.length > 1 && activeIdx === i && (
                <span onClick={() => { if (window.confirm('确定删除此大屏及其所有图表？')) delDashboard(i) }} style={{ cursor: 'pointer', color: '#e74c3c', fontSize: 14 }}>×</span>
              )}
            </div>
          ))}
          <button onClick={addDashboard} style={{ padding: '4px 8px', fontSize: 11, cursor: 'pointer', background: 'var(--bg-input)', border: '1px dashed var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>+ 新建大屏</button>
        </div>
        {/* 标题 */}
        <div style={{ textAlign: 'center', padding: '4px 16px 0' }}>
          {renaming ? (
            <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
              <input value={renameVal} onChange={e => setRenameVal(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') doRename() }} autoFocus style={{ padding: '4px 10px', fontSize: 18, fontWeight: 600, borderRadius: 6, background: 'var(--bg-input)', border: '1px solid var(--accent)', color: 'var(--text-primary)', outline: 'none', textAlign: 'center', width: 220 }} />
              <button onClick={doRename} style={{ padding: '4px 10px', fontSize: 12, cursor: 'pointer', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 4 }}>✓</button>
            </div>
          ) : (
            <h2 style={{ fontSize: 20, fontWeight: 700, margin: 0, cursor: 'pointer', color: 'var(--text-primary)' }} onClick={() => { setRenameVal(active.name); setRenaming(true) }}>{active.name}</h2>
          )}
        </div>
        {/* 统一配色 */}
        {items.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 16px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>统一配色:</span>
            <select value={previewColor ?? ''} onChange={e => setPreviewColor(e.target.value === '' ? null : Number(e.target.value))} style={selectStyle}>
              <option value="">——</option>
              {COLOR_SCHEMES.map((s, i) => <option key={s.name} value={i}>{s.name}</option>)}
            </select>
            {previewColor !== null && (
              <>
                <button onClick={applyTheme} style={{ padding: '3px 12px', fontSize: 11, cursor: 'pointer', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6 }}>确定</button>
                <button onClick={cancelPreview} style={{ padding: '3px 12px', fontSize: 11, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>取消</button>
              </>
            )}
          </div>
        )}
        {/* 工具栏 */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '4px 16px', flexWrap: 'wrap' }}>
          <button onClick={() => { setBuilderVisible(!builderVisible); setMetricVisible(false) }} style={{ padding: '4px 12px', fontSize: 12, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>{builderVisible ? '收起' : '+ 添加图表'}</button>
          <button onClick={() => { setMetricVisible(!metricVisible); setBuilderVisible(false) }} style={{ padding: '4px 12px', fontSize: 12, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>{metricVisible ? '收起' : '+ 添加指标'}</button>
          <button onClick={toggleFullscreen} style={{ padding: '4px 12px', fontSize: 12, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>{isFullscreen ? '退出全屏' : '全屏'}</button>
        </div>
        {/* 图表构建器 */}
        {builderVisible && (
          <div style={{ margin: '4px 16px 8px', padding: 12, background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
            <div><Label>主表</Label><select value={builderForm.table} onChange={e => { setBuilderForm(f => ({ ...f, table: e.target.value, xCol: '', yCol: '' })); loadColumns(e.target.value) }} style={selectStyle}><option value="">选择表</option>{tables.map(t => <option key={t} value={t}>{t}</option>)}</select></div>
            <div><Label>X 轴</Label><select value={builderForm.xCol} onChange={e => setBuilderForm(f => ({ ...f, xCol: e.target.value }))} style={selectStyle}><option value="">选择列</option>{columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}</select></div>
            <div><Label>Y 轴</Label><select value={builderForm.yCol} onChange={e => setBuilderForm(f => ({ ...f, yCol: e.target.value }))} style={selectStyle}><option value="">选择列</option>{columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}<option value="COUNT(*)">COUNT(*)</option><option value="SUM(*)">SUM</option><option value="AVG(*)">AVG</option></select></div>
            <div><Label>图表类型</Label><select value={builderForm.chartType} onChange={e => setBuilderForm(f => ({ ...f, chartType: e.target.value }))} style={selectStyle}><option value="bar">柱状图</option><option value="line">折线图</option><option value="pie">饼图</option><option value="scatter">散点图</option></select></div>
            <div><Label>标题</Label><input value={builderForm.title} onChange={e => setBuilderForm(f => ({ ...f, title: e.target.value }))} placeholder="可选" style={smallInputStyle} /></div>
            <button onClick={addCustomChart} disabled={!builderForm.table || !builderForm.xCol || !builderForm.yCol} style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, border: 'none', background: builderForm.table && builderForm.xCol && builderForm.yCol ? 'var(--accent)' : 'var(--bg-hover)', color: builderForm.table && builderForm.xCol && builderForm.yCol ? '#fff' : 'var(--text-muted)', cursor: builderForm.table && builderForm.xCol && builderForm.yCol ? 'pointer' : 'not-allowed' }}>添加</button>
            <button onClick={() => { setBuilderVisible(false); setBuilderForm({ table: '', xCol: '', yCol: '', chartType: 'bar', title: '' }) }} style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, border: '1px solid var(--border-color)', background: 'var(--bg-input)', color: 'var(--text-muted)', cursor: 'pointer' }}>取消</button>
          </div>
        )}
        {/* 指标构建器 */}
        {metricVisible && (
          <div style={{ margin: '4px 16px 8px', padding: 12, background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
            <div><Label>表</Label><select value={metricForm.table} onChange={e => { setMetricForm(f => ({ ...f, table: e.target.value, column: '' })); loadColumns(e.target.value) }} style={selectStyle}><option value="">选择表</option>{tables.map(t => <option key={t} value={t}>{t}</option>)}</select></div>
            <div><Label>列</Label><select value={metricForm.column} onChange={e => setMetricForm(f => ({ ...f, column: e.target.value }))} style={selectStyle}><option value="">*</option>{columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}</select></div>
            <div><Label>聚合</Label><select value={metricForm.aggFunc} onChange={e => setMetricForm(f => ({ ...f, aggFunc: e.target.value }))} style={selectStyle}><option value="COUNT">COUNT</option><option value="SUM">SUM</option><option value="AVG">AVG</option><option value="MAX">MAX</option><option value="MIN">MIN</option></select></div>
            <div><Label>过滤</Label><input value={metricForm.filter} onChange={e => setMetricForm(f => ({ ...f, filter: e.target.value }))} placeholder="如 status='已完成'" style={smallInputStyle} /></div>
            <div><Label>标题</Label><input value={metricForm.title} onChange={e => setMetricForm(f => ({ ...f, title: e.target.value }))} placeholder="可选" style={smallInputStyle} /></div>
            <button onClick={addMetric} disabled={!metricForm.table} style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, border: 'none', background: metricForm.table ? 'var(--accent)' : 'var(--bg-hover)', color: metricForm.table ? '#fff' : 'var(--text-muted)', cursor: metricForm.table ? 'pointer' : 'not-allowed' }}>添加指标</button>
            <button onClick={() => { setMetricVisible(false); setMetricForm({ table: '', column: '', aggFunc: 'COUNT', filter: '', title: '' }) }} style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, border: '1px solid var(--border-color)', background: 'var(--bg-input)', color: 'var(--text-muted)', cursor: 'pointer' }}>取消</button>
          </div>
        )}
      </div>

      {/* 指标卡片区（不参与排序，宽度自适应内容） */}
      {metricItems.length > 0 && (
        <div style={{ display: 'flex', gap: 12, padding: '0 16px 8px', flexWrap: 'wrap', flexShrink: 0 }}>
          {metricItems.map(item => (
            <div key={item.id} style={{ background: 'var(--bg-card)', borderRadius: 10, padding: '12px 16px', position: 'relative', flex: 'none', width: 'fit-content', minWidth: 120 }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{item.title}</div>
              <div style={{ fontSize: 28, fontWeight: 600, color: 'var(--text-primary)' }}>{item.metricValue}</div>
              <span onClick={() => removeItem(item.id)} style={{ position: 'absolute', top: 6, right: 8, cursor: 'pointer', color: '#e74c3c', fontSize: 14 }}>✕</span>
            </div>
          ))}
        </div>
      )}

      {/* 可拖拽排序的图表网格 */}
      <DndContext collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={chartItems.map(i => i.id)} strategy={verticalListSortingStrategy}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16, padding: '0 16px 16px' }}>
            {chartItems.map(item => (
              <SortableChartCard
                key={item.id}
                item={item}
                onRemove={removeItem}
                effectiveColor={previewColor ?? item.manualColor ?? 0}
                onResize={handleResize}
                onUpdateColor={handleUpdateColor}
              />
            ))}
            {items.length === 0 && !builderVisible && !metricVisible && (
              <div style={{ gridColumn: '1 / -1', textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>
                <div style={{ fontSize: 32, marginBottom: 12 }}>📊</div>
                <div>数据大屏为空</div>
                <div style={{ fontSize: 12, marginTop: 4 }}>点击「添加图表」或「添加指标」创建内容</div>
              </div>
            )}
          </div>
        </SortableContext>
      </DndContext>

      {/* 删除确认弹窗 */}
      {confirmDeleteId && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 10000, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.4)' }} onClick={() => setConfirmDeleteId(null)}>
          <div style={{ background: 'var(--bg-card)', borderRadius: 12, padding: 24, minWidth: 280, boxShadow: '0 8px 32px rgba(0,0,0,0.2)' }} onClick={e => e.stopPropagation()}>
            <h3 style={{ fontSize: 15, fontWeight: 500, margin: '0 0 8px', color: 'var(--text-primary)', textAlign: 'center' }}>确认删除</h3>
            <p style={{ fontSize: 13, color: 'var(--text-muted)', textAlign: 'center', margin: '0 0 20px' }}>确定要删除这个图表吗？</p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button onClick={doRemove} style={{ padding: '8px 28px', fontSize: 13, cursor: 'pointer', background: '#e74c3c', color: '#fff', border: 'none', borderRadius: 8, fontWeight: 500 }}>删除</button>
              <button onClick={() => setConfirmDeleteId(null)} style={{ padding: '8px 28px', fontSize: 13, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 8, color: 'var(--text-muted)' }}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// -----------------------------------------------------------------------------
// 系统监控面板
// -----------------------------------------------------------------------------
function MonitorPanel() {
  const [stats, setStats] = useState(null)

  useEffect(() => {
    fetch('/api/cache/stats').then(r => r.json()).then(setStats).catch(() => {})
  }, [])

  const gaugeOption = stats ? {
    series: [{
      type: 'gauge', startAngle: 200, endAngle: -20, min: 0, max: 100, splitNumber: 5,
      progress: { show: true, width: 8 }, axisLine: { lineStyle: { width: 8 } },
      axisTick: { show: false }, splitLine: { length: 8, lineStyle: { width: 2 } },
      axisLabel: { distance: 20, color: '#8892a8' },
      detail: { formatter: '{value}%', fontSize: 18, color: 'var(--text-primary)' },
      data: [{ value: +(stats.hit_rate || 0).toFixed(1), name: '缓存命中率' }],
    }],
  } : null

  const trendLabels = ['过去7天', '过去6天', '过去5天', '过去4天', '过去3天', '昨天', '今天']
  const trendData = [12, 18, 15, 22, 20, 28, stats?.total_queries || 0]
  const trendOption = {
    tooltip: { trigger: 'axis' }, grid: { left: 40, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: trendLabels, axisLabel: { color: '#8892a8', fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { color: '#8892a8' } },
    series: [{ type: 'line', data: trendData, smooth: true, lineStyle: { color: '#8A9BAE', width: 2 }, areaStyle: { color: 'rgba(138,155,174,0.15)' }, itemStyle: { color: '#8A9BAE' } }],
  }

  return (
    <motion.div variants={panelVariants} initial="hidden" animate="visible" style={{ padding: 20 }}>
      <h2 style={{ fontSize: 16, fontWeight: 500, marginBottom: 12 }}>系统监控</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        {[
          { label: '缓存命中率', value: stats ? (stats.hit_rate ?? 0).toFixed(1) + '%' : '-' },
          { label: 'L1 命中率', value: stats ? (stats.l1_hit_rate ?? 0).toFixed(1) + '%' : '-' },
          { label: 'L2 命中率', value: stats ? (stats.l2_hit_rate ?? 0).toFixed(1) + '%' : '-' },
          { label: '总查询次数', value: stats?.total_queries ?? '-' },
        ].map(item => (
          <div key={item.label} style={{ padding: 16, borderRadius: 12, background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
            <div style={{ fontSize: 18, fontWeight: 500, color: 'var(--text-primary)' }}>{item.value}</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>{item.label}</div>
          </div>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 12, padding: 16 }}>
          <h3 style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 8 }}>请求趋势（近 7 天）</h3>
          <ReactEChartsCore option={trendOption} style={{ height: 200, width: '100%' }} />
        </div>
        {gaugeOption && (
          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 12, padding: 16 }}>
            <h3 style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 8 }}>缓存命中率</h3>
            <ReactEChartsCore option={gaugeOption} style={{ height: 220, width: '100%' }} />
          </div>
        )}
      </div>
    </motion.div>
  )
}

// -----------------------------------------------------------------------------
// 表格信息面板
// -----------------------------------------------------------------------------
function TablesPanel() {
  const [tables, setTables] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedTable, setSelectedTable] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetch('/api/db/status')
      .then(r => r.json())
      .then(d => setTables(d.tables || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const loadTableDetail = async (tableName) => {
    setDetailLoading(true)
    setSelectedTable({ name: tableName, schema: null, preview: null })
    try {
      const [schemaData, previewData] = await Promise.all([
        fetch(`/api/dashboard/table-schema?table=${encodeURIComponent(tableName)}`).then(r => r.json()),
        fetch(`/api/dashboard/table-preview?table=${encodeURIComponent(tableName)}`).then(r => r.json()),
      ])
      setSelectedTable({
        name: tableName,
        schema: schemaData.columns || [],
        preview: { columns: previewData.columns || [], rows: previewData.rows || [] },
      })
    } catch (e) {
      setSelectedTable({ name: tableName, schema: [], preview: null, error: e.message })
    } finally {
      setDetailLoading(false)
    }
  }

  return (
    <motion.div variants={panelVariants} initial="hidden" animate="visible" style={{ padding: 20, display: 'flex', gap: 20, height: '100%' }}>
      <div style={{ width: 260, flexShrink: 0 }}>
        <h2 style={{ fontSize: 16, fontWeight: 500, marginBottom: 12 }}>数据库表</h2>
        {loading ? (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>加载中...</p>
        ) : tables.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>请先连接数据库</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {tables.map(t => (
              <button key={t} onClick={() => loadTableDetail(t)} style={{
                padding: '8px 12px', borderRadius: 8, cursor: 'pointer', textAlign: 'left',
                background: selectedTable?.name === t ? 'rgba(138,155,174,0.15)' : 'var(--bg-card)',
                border: selectedTable?.name === t ? '1px solid var(--accent)' : '1px solid var(--border-color)',
                fontSize: 13, color: 'var(--text-primary)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <span>📄 {t}</span>
                <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>▸</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div style={{ flex: 1, overflow: 'auto' }}>
        {!selectedTable ? (
          <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: 20, textAlign: 'center' }}>点击左侧表名查看详情</div>
        ) : detailLoading ? (
          <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: 20 }}>加载中...</div>
        ) : (
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 500, marginBottom: 12 }}>📄 {selectedTable.name}</h2>
            {selectedTable.schema && (
              <div style={{ marginBottom: 20 }}>
                <h3 style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 8 }}>表结构</h3>
                <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 8, overflow: 'hidden' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                      <tr style={{ background: 'var(--bg-secondary)' }}>
                        <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-muted)', borderBottom: '1px solid var(--border-color)' }}>列名</th>
                        <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-muted)', borderBottom: '1px solid var(--border-color)' }}>类型</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedTable.schema.map(col => (
                        <tr key={col.name} style={{ borderBottom: '1px solid var(--border-color)' }}>
                          <td style={{ padding: '6px 12px', color: 'var(--text-primary)' }}>{col.name}</td>
                          <td style={{ padding: '6px 12px', color: 'var(--text-muted)' }}>{col.type}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            {selectedTable.preview && (
              <div>
                <h3 style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 8 }}>数据预览 (前 {selectedTable.preview.rows.length} 行)</h3>
                <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 8, overflow: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: 'var(--bg-secondary)' }}>
                        {selectedTable.preview.columns.map(c => (
                          <th key={c} style={{ padding: '6px 10px', textAlign: 'left', color: 'var(--text-muted)', borderBottom: '1px solid var(--border-color)', whiteSpace: 'nowrap' }}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {selectedTable.preview.rows.map((row, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid var(--border-color)' }}>
                          {row.map((cell, j) => (
                            <td key={j} style={{ padding: '4px 10px', color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>{cell ?? ''}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </motion.div>
  )
}

// -----------------------------------------------------------------------------
// 评估报告面板
// -----------------------------------------------------------------------------
function EvalPanel() {
  const [report, setReport] = useState(null)
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState({ completed: 0, total: 0, current: '' })
  const [results, setResults] = useState([])
  const esRef = useRef(null)

  useEffect(() => {
    fetch('/api/eval/report').then(r => r.json()).then(d => { if (!d.error) setReport(d) }).catch(() => {})
  }, [])

  const startEval = () => {
    setRunning(true); setResults([]); setReport(null); setProgress({ completed: 0, total: 0, current: '' })
    try {
      const es = new EventSource('/api/eval/run-stream')
      esRef.current = es
      es.addEventListener('progress', (e) => { const d = JSON.parse(e.data); setProgress(d) })
      es.addEventListener('result', (e) => { const d = JSON.parse(e.data); setResults(prev => [...prev, d]) })
      es.addEventListener('done', (e) => { const d = JSON.parse(e.data); setReport(d.report); setRunning(false); es.close() })
      es.onerror = () => { setRunning(false); es.close() }
    } catch { setRunning(false) }
  }

  const catMetrics = report?.category_metrics
  const catNames = catMetrics ? Object.keys(catMetrics) : []
  const catValues = catNames.map(n => catMetrics[n]?.accuracy || 0)
  const barOption = catNames.length > 0 ? {
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: catNames, axisLabel: { color: '#8892a8' } },
    yAxis: { type: 'value', max: 100, axisLabel: { color: '#8892a8', formatter: '{value}%' } },
    series: [{ type: 'bar', data: catValues, itemStyle: { color: '#8A9BAE' } }],
    grid: { left: 50, right: 20, top: 20, bottom: 30 },
  } : null

  return (
    <motion.div variants={panelVariants} initial="hidden" animate="visible" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h2 style={{ fontSize: 16, fontWeight: 500, margin: 0 }}>评估报告</h2>
        <button onClick={startEval} disabled={running} style={{ padding: '4px 14px', fontSize: 12, cursor: running ? 'not-allowed' : 'pointer', background: running ? 'var(--bg-hover)' : 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6 }}>
          {running ? '评估中...' : '开始评估'}
        </button>
      </div>
      {running && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
            <span>{progress.current || '准备中...'}</span>
            <span>{progress.completed}/{progress.total}</span>
          </div>
          <progress value={progress.completed} max={progress.total} style={{ width: '100%', height: 6, borderRadius: 3, accentColor: 'var(--accent)' }} />
        </div>
      )}
      {results.length > 0 && (
        <div style={{ maxHeight: 200, overflow: 'auto', background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 8, padding: 8, marginBottom: 16 }}>
          {results.map((r, i) => (
            <div key={i} style={{ padding: '2px 0', display: 'flex', gap: 6, color: 'var(--text-secondary)' }}>
              <span>{r.status === '✓' ? '✅' : '❌'}</span>
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.question}</span>
              <span style={{ color: r.sql_valid ? '#7EB87E' : '#e74c3c', flexShrink: 0 }}>SQL{r.sql_valid ? '✓' : '✗'}</span>
              <span style={{ color: r.execution_match ? '#7EB87E' : '#e74c3c', flexShrink: 0 }}>结果{r.execution_match ? '✓' : '✗'}</span>
            </div>
          ))}
        </div>
      )}
      {!report ? (
        !running && <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>尚未运行评估</p>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 20 }}>
            {[
              { label: '执行准确率', value: report.overall_metrics?.execution_accuracy ?? '-' },
              { label: 'SQL 语法正确率', value: report.overall_metrics?.sql_syntax_validity ?? '-' },
              { label: '测试用例数', value: report.overall_metrics?.total_valid ?? '-' },
            ].map(item => (
              <div key={item.label} style={{ padding: 16, borderRadius: 12, background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
                <div style={{ fontSize: 18, fontWeight: 500, color: 'var(--text-primary)' }}>{item.value}</div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>{item.label}</div>
              </div>
            ))}
          </div>
          {barOption && (
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 12, padding: 16 }}>
              <h3 style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 8 }}>分类准确率</h3>
              <ReactEChartsCore option={barOption} style={{ height: 250, width: '100%' }} />
            </div>
          )}
        </>
      )}
    </motion.div>
  )
}

// -----------------------------------------------------------------------------
// TabPanels 导出
// -----------------------------------------------------------------------------
function TabPanels({ activeTab }) {
  switch (activeTab) {
    case 'dashboard': return <DashboardPanel />
    case 'monitor': return <MonitorPanel />
    case 'tables': return <TablesPanel />
    case 'eval': return <EvalPanel />
    default: return null
  }
}

export default TabPanels