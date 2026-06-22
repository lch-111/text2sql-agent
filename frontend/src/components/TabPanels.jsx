import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react'
import { motion } from 'framer-motion'
import ReactEChartsCore from 'echarts-for-react'
import * as echarts from 'echarts'

// 动态加载 react-grid-layout（用 var 避免模块顶层 TDZ）
var GridLayout = lazy(() => import('react-grid-layout').then(m => ({ default: m.default })))
import 'react-grid-layout/css/styles.css'

/* ============================================================================
   模块顶层先用 var 声明（var 无 TDZ，可安全引用），避免 Rolldown 编译后出现
   "Cannot access 'L/G/K' before initialization" 错误。
   ============================================================================ */
var panelVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.3 } },
}
/** Label 辅助组件（函数声明完全 hoisted，无 TDZ）*/
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
var selStyle2 = {
  padding: '2px 6px', fontSize: 10, borderRadius: 4,
  background: 'var(--bg-input)', border: '1px solid var(--border-color)',
  color: 'var(--text-primary)', outline: 'none',
}

/** HEX 颜色转 RGBA（支持透明度）*/
function hexToRgba(hex, alpha) {
  if (!hex || hex === 'transparent' || hex.startsWith('rgba')) return hex
  const clean = hex.replace('#', '')
  if (clean.length < 6) return hex
  const r = parseInt(clean.slice(0, 2), 16)
  const g = parseInt(clean.slice(2, 4), 16)
  const b = parseInt(clean.slice(4, 6), 16)
  if (isNaN(r) || isNaN(g) || isNaN(b)) return hex
  return `rgba(${r},${g},${b},${alpha})`
}

/* ============================================================================
   数据大屏 — 多屏管理 + 用户自建图表/指标 + GridLayout 拖拽 + 全屏
   ============================================================================ */
function DashboardPanel() {
  const DASHBOARDS_KEY = 'dashboard_manager'
  const CUSTOM_STORAGE_KEY = 'dashboard_custom_charts'
  const containerRef = useRef(null)
  const dashboardRef = useRef(null)
  const headerRef = useRef(null)
  const [dashSize, setDashSize] = useState({ w: 1200, h: 800 })
  const [headerH, setHeaderH] = useState(180)

  // 多屏列表
  const [dashboards, setDashboards] = useState(() => {
    try { return JSON.parse(localStorage.getItem(DASHBOARDS_KEY) || 'null') || [{ id: 'default', name: '数据看板 1', layout: [], items: [] }] }
    catch { return [{ id: 'default', name: '数据看板 1', layout: [], items: [] }] }
  })
  const [activeIdx, setActiveIdx] = useState(0)
  const active = dashboards[activeIdx] || dashboards[0]
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameVal, setRenameVal] = useState('')

  // 当前大屏的 layout & items
  const [layout, setLayout] = useState(active.layout || [])
  const [items, setItems] = useState(active.items || [])

  // 同步到 active dashboard
  const syncDashboards = (newLayout, newItems) => {
    setDashboards(prev => {
      const next = [...prev]
      next[activeIdx] = { ...next[activeIdx], layout: newLayout ?? layout, items: newItems ?? items }
      localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
      return next
    })
  }

  // active dashboard 切换时加载对应数据
  useEffect(() => {
    const d = dashboards[activeIdx] || dashboards[0]
    setLayout(d.layout || [])
    setItems(d.items || [])
    setSavedOpacity(d.opacity ?? 1)
    setPreviewColor(null)
    setPreviewOpacity(null)
    setThemeStack([])
  }, [activeIdx])

  // 图表构建器
  const [builderVisible, setBuilderVisible] = useState(false)
  const [metricVisible, setMetricVisible] = useState(false)
  const [tables, setTables] = useState([])
  const [columns, setColumns] = useState([])
  const [builderForm, setBuilderForm] = useState({ table: '', xCol: '', yCol: '', chartType: 'bar', title: '', joinTable: '', joinOn: '', aggFunc: '' })
  const [metricForm, setMetricForm] = useState({ table: '', column: '', aggFunc: 'COUNT', filter: '', title: '' })
  const [customCharts, setCustomCharts] = useState([])

  useEffect(() => {
    fetch('/api/db/status').then(r => r.json()).then(d => setTables(d.tables || [])).catch(() => {})

    // 读取暂存的待添加项（支持目标大屏）
    try {
      const pending = localStorage.getItem('pending_dashboard_item')
      if (pending) {
        const item = JSON.parse(pending)
        localStorage.removeItem('pending_dashboard_item')
        if (item) {
          const id = 'pending_' + Date.now()
          const itemType = item.type || 'chart'
          const newItem = { ...item, id, type: itemType }
          const targetName = item.targetDashboard
          const isMetric = itemType === 'metric'
          const layoutSize = isMetric ? { w: 3, h: 1 } : { w: 3, h: 4 }

          if (targetName) {
            // 添加到指定大屏
            setDashboards(prev => {
              const next = [...prev]
              const targetIdx = next.findIndex(d => d.name === targetName)
              if (targetIdx >= 0) {
                const target = next[targetIdx]
                const newItems = [...(target.items || []), newItem]
                const newLayout = [...(target.layout || []), { i: id, x: 0, y: 100, ...layoutSize }]
                next[targetIdx] = { ...target, items: newItems, layout: newLayout }
                localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
              }
              return next
            })
          } else {
            const newItems = [...items, newItem]
            const newLayout = [...layout, { i: id, x: 0, y: 100, ...layoutSize }]
            setItems(newItems); setLayout(newLayout)
            setTimeout(() => syncDashboards(newLayout, newItems), 50)
          }
        }
      }
    } catch {}
  }, [])

  // 监听仪表板容器尺寸变化，响应式适配
  useEffect(() => {
    const el = dashboardRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        setDashSize({ w: entry.contentRect.width, h: entry.contentRect.height })
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // 实时测量顶部控件区域高度，确保图表区准确填满剩余空间
  useEffect(() => {
    if (headerRef.current) setHeaderH(headerRef.current.offsetHeight)
  }, [items.length, builderVisible, metricVisible, previewColor, previewOpacity, themeStack.length])

  // 监听从 ChatArea 发来的添加到大屏事件（支持目标大屏选择）
  useEffect(() => {
    const handler = (e) => {
      const item = e.detail; if (!item) return
      const id = 'custom_' + Date.now()
      const itemType = item.type || 'chart'
      const newItem = { ...item, id, type: itemType }
      const targetName = item.targetDashboard
      const isMetric = itemType === 'metric'
      const layoutSize = isMetric ? { w: 3, h: 1 } : { w: 3, h: 4 }

      if (targetName) {
        // 添加到指定大屏
        setDashboards(prev => {
          const next = [...prev]
          const targetIdx = next.findIndex(d => d.name === targetName)
          if (targetIdx >= 0) {
            const target = next[targetIdx]
            const newItems = [...(target.items || []), newItem]
            const newLayout = [...(target.layout || []), { i: id, x: 0, y: 100, ...layoutSize }]
            next[targetIdx] = { ...target, items: newItems, layout: newLayout }
            localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
          }
          return next
        })
      } else {
        // 无目标时添加到当前大屏
        const newItems = [...items, newItem]
        const newLayout = [...layout, { i: id, x: 0, y: 100, ...layoutSize }]
        setItems(newItems); setLayout(newLayout)
        // 这里不同步 syncDashboards 因为 setState 是异步的；用 setTimeout
        setTimeout(() => {
          setDashboards(prev => {
            const next = [...prev]
            next[activeIdx] = { ...next[activeIdx], items: newItems, layout: newLayout }
            localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
            return next
          })
        }, 50)
      }
    }
    window.addEventListener('add-to-dashboard', handler)
    return () => window.removeEventListener('add-to-dashboard', handler)
  }, [items, layout, activeIdx])

  const onLayoutChange = useCallback((newLayout) => {
    setLayout(newLayout); syncDashboards(newLayout, items)
  }, [items])

  // 新建大屏
  const addDashboard = () => {
    setDashboards(prev => {
      const next = [...prev, { id: 'db_' + Date.now(), name: `数据看板 ${prev.length + 1}`, layout: [], items: [] }]
      localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
      return next
    })
    setActiveIdx(dashboards.length)
  }

  // 删除大屏
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
    if (!isFullscreen) { containerRef.current?.requestFullscreen?.(); setIsFullscreen(true) }
    else { document.exitFullscreen?.(); setIsFullscreen(false) }
  }

  // 选择表后加载列
  const loadColumns = async (table) => {
    if (!table) return
    try { const r = await fetch(`/api/dashboard/table-schema?table=${encodeURIComponent(table)}`); const d = await r.json(); setColumns(d.columns || []) }
    catch { setColumns([]) }
  }

  // 添加图表（支持多表 JOIN）
  const addCustomChart = async () => {
    const { table, xCol, yCol, chartType, title, joinTable, joinOn, aggFunc } = builderForm
    if (!table || !xCol || !yCol) return
    try {
      let queryTable = table
      if (joinTable && joinOn) queryTable = `${table} JOIN ${joinTable} ON ${joinOn}`
      const payload = { table: queryTable, x_column: xCol, y_column: yCol, chart_type: chartType, limit: 100 }
      if (aggFunc) payload.aggregation = aggFunc
      const r = await fetch('/api/dashboard/chart-data', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      const chartDataJson = await r.json()
      const id = 'builder_' + Date.now()
      const chartItem = {
        id,
        title: title || `${table} - ${xCol} × ${yCol}`,
        chartData: chartDataJson,
        chartType,
        table,
        xCol,
        yCol,
      }
      setItems(prev => [...prev, chartItem])
      setCustomCharts(prev => {
        const next = [...prev, chartItem]
        localStorage.setItem(CUSTOM_STORAGE_KEY, JSON.stringify(next))
        return next
      })
      setLayout(prev => [...prev, { i: id, x: 0, y: 100, w: 3, h: 4 }])
      setBuilderVisible(false)
      setBuilderForm({ table: '', xCol: '', yCol: '', chartType: 'bar', title: '' })
    } catch {}
  }

  const removeCustomChart = (id) => {
    setItems(prev => prev.filter(i => i.id !== id))
    setCustomCharts(prev => {
      const next = prev.filter(c => c.id !== id)
      localStorage.setItem(CUSTOM_STORAGE_KEY, JSON.stringify(next))
      return next
    })
    setLayout(prev => prev.filter(l => l.i !== id))
  }

  // 构建图表 option
  // 添加指标
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
      const metricItem = { id, title: title || `${aggFunc}(${column || '*'})`, metricValue: d.values?.[0] ?? '—', type: 'metric' }
      setItems(prev => {
        const next = [...prev, metricItem]
        localStorage.setItem(CUSTOM_STORAGE_KEY, JSON.stringify(next))
        return next
      })
      setLayout(prev => [...prev, { i: id, x: 0, y: 100, w: 3, h: 1 }])
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
      localStorage.setItem(CUSTOM_STORAGE_KEY, JSON.stringify(next))
      return next
    })
    setLayout(prev => prev.filter(l => l.i !== confirmDeleteId))
    setConfirmDeleteId(null)
  }

  // 18 种图表 + 21 配色（复用 ChatArea 定义）
  const CHART_TYPES = [
    { key: 'bar', label: '📊 柱状图' }, { key: 'line', label: '📈 折线图' }, { key: 'pie', label: '🥧 饼图' },
    { key: 'scatter', label: '🔵 散点图' }, { key: 'funnel', label: '🔻 漏斗图' }, { key: 'radar', label: '🕸️ 雷达图' },
    { key: 'heatmap', label: '🔥 热力图' }, { key: 'treemap', label: '🗂️ 矩形树图' }, { key: 'sunburst', label: '🌅 旭日图' },
    { key: 'sankey', label: '🔀 桑基图' }, { key: 'boxplot', label: '📦 箱线图' }, { key: 'candlestick', label: '🕯️ K线图' },
    { key: 'effectScatter', label: '✨ 特效散点' }, { key: 'lines', label: '〰️ 轨迹图' }, { key: 'parallel', label: '📊 平行坐标' },
    { key: 'graph', label: '🔗 关系图' }, { key: 'gauge', label: '🎯 仪表盘' }, { key: 'pictorialBar', label: '🎨 象形柱图' },
  ]
  const COLOR_SCHEMES = [
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

  // 构建图表 ECharts option（支持 18 种类型 + 21 配色 + 横纵轴标签）
  // ---- 统一改色 + 透明度 + 撤销栈 ----
  const MAX_STACK = 3
  const [previewColor, setPreviewColor] = useState(null)     // 选择中的配色预览（未保存）
  const [previewOpacity, setPreviewOpacity] = useState(null) // 选择中的透明度预览（未保存）
  const [savedOpacity, setSavedOpacity] = useState(() => active.opacity ?? 1) // 已保存的透明度
  const [themeStack, setThemeStack] = useState([])            // 撤销栈 [{ manualColors: {id: idx}, opacity }]

  /** 确定：保存当前预览，备份到撤销栈 */
  const applyTheme = () => {
    const snapshot = {}
    items.forEach(it => { snapshot[it.id] = it.manualColor })
    const newOpacity = previewOpacity ?? savedOpacity

    setThemeStack(prev => {
      const next = [...prev, { manualColors: snapshot, opacity: savedOpacity }]
      return next.length > MAX_STACK ? next.slice(-MAX_STACK) : next
    })

    setItems(prev => prev.map(it => ({
      ...it,
      manualColor: previewColor ?? it.manualColor,
    })))
    setSavedOpacity(newOpacity)
    setPreviewColor(null)
    setPreviewOpacity(null)
    // 持久化透明度到大屏对象
    setDashboards(dPrev => {
      const next = [...dPrev]
      next[activeIdx] = { ...next[activeIdx], items: items.map(it => ({ ...it, manualColor: previewColor ?? it.manualColor })), layout, opacity: newOpacity }
      localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(next))
      return next
    })
  }

  /** 取消：清空预览，回退到已保存状态 */
  const cancelPreview = () => {
    setPreviewColor(null)
    setPreviewOpacity(null)
  }

  /** 恢复上一步：从撤销栈弹出最近一次 */
  const undoThemeStep = () => {
    setThemeStack(prev => {
      if (prev.length === 0) return prev
      const next = [...prev]
      const last = next.pop()
      // 恢复 manualColor
      setItems(prevItems => prevItems.map(it => ({
        ...it,
        manualColor: last.manualColors[it.id] ?? it.manualColor,
      })))
      // 恢复透明度
      const restoredOpacity = last.opacity !== undefined ? last.opacity : 1
      setSavedOpacity(restoredOpacity)
      // 持久化
      setDashboards(dPrev => {
        const dNext = [...dPrev]
        dNext[activeIdx] = {
          ...dNext[activeIdx],
          items: items.map(it => ({ ...it, manualColor: last.manualColors[it.id] ?? it.manualColor })),
          layout,
          opacity: restoredOpacity,
        }
        localStorage.setItem(DASHBOARDS_KEY, JSON.stringify(dNext))
        return dNext
      })
      return next
    })
  }

  // 选择配色时立即预览
  const handleThemeSelect = (value) => {
    const newColor = value === '' ? null : Number(value)
    setPreviewColor(newColor)
  }

  // 响应图表容器 resize
  const [resizeVer, setResizeVer] = useState(0)
  const onGridResize = useCallback((_layout, _oldItem, newItem) => {
    setResizeVer(v => v + 1)
    setTimeout(() => {
      const container = document.querySelector(`[data-echart-id="${newItem.i}"]`)
      if (container) {
        const canvas = container.querySelector('canvas')
        if (canvas && canvas.parentElement) {
          const instance = echarts?.getInstanceByDom(canvas.parentElement)
          if (instance) instance.resize()
        }
      }
    }, 100)
  }, [])

  const makeChartOption = (item, heightLevel = 'normal') => {
    const d = item.chartData
    if (!d || !d.labels) return null
    // 单个图表手动配色优先 → 全局预览配色 → 已保存配色 → 默认
    const effectiveColor = previewColor ?? item.manualColor ?? 0
    const colors = COLOR_SCHEMES[effectiveColor]?.colors || COLOR_SCHEMES[0].colors
    const ct = item.chartType || 'bar'
    const labels = d.labels
    const values = d.values
    const xName = item.xCol || ''
    const yName = item.yCol || ''

    // 根据高度级别调整 grid 边距、轴标签旋转、字号
    let grid, axisLabel, labelSize, nameVisible
    if (heightLevel === 'compact') {
      grid = { left: 30, right: 15, top: 15, bottom: 30 }
      axisLabel = { color: '#8892a8', rotate: 90, fontSize: 10 }
      labelSize = 10
      nameVisible = false
    } else if (heightLevel === 'spacious') {
      grid = { left: 70, right: 35, top: 35, bottom: 70 }
      axisLabel = { color: '#8892a8', rotate: labels.length > 12 ? 40 : 0, fontSize: 13 }
      labelSize = 13
      nameVisible = true
    } else {
      grid = { left: 55, right: 25, top: 25, bottom: 55 }
      axisLabel = { color: '#8892a8', rotate: labels.length > 8 ? 40 : 0, fontSize: 11 }
      labelSize = 11
      nameVisible = true
    }

    const base = { tooltip: { trigger: ct === 'pie' ? 'item' : 'axis' }, grid, color: colors }

    if (ct === 'pie') return { ...base, series: [{ type: 'pie', data: labels.map((l, i) => ({ name: l, value: values[i] })), label: { color: '#8892a8', fontSize: labelSize } }] }
    if (ct === 'scatter' || ct === 'effectScatter') return { ...base, xAxis: { type: 'value', name: nameVisible ? xName : '', axisLabel: { color: '#8892a8', fontSize: labelSize } }, yAxis: { type: 'value', name: nameVisible ? yName : '', axisLabel: { color: '#8892a8', fontSize: labelSize } }, series: [{ type: ct, data: labels.map((l, i) => [Number(l) || 0, Number(values[i]) || 0]) }] }
    if (ct === 'funnel') return { ...base, series: [{ type: 'funnel', data: labels.map((l, i) => ({ name: l, value: values[i] })), label: { fontSize: labelSize } }] }
    if (ct === 'radar') return { ...base, radar: { indicator: labels.map(l => ({ name: l })) }, series: [{ type: 'radar', data: [{ value: values }] }] }
    if (ct === 'heatmap') return { ...base, xAxis: { type: 'category', data: labels, axisLabel: { ...axisLabel, rotate: 90 } }, yAxis: { type: 'category', data: ['value'], axisLabel: { color: '#8892a8', fontSize: labelSize } }, visualMap: { min: Math.min(...values), max: Math.max(...values), textStyle: { fontSize: labelSize } }, series: [{ type: 'heatmap', data: labels.map((l, i) => [i, 0, values[i]]) }] }
    if (ct === 'treemap' || ct === 'sunburst') return { ...base, series: [{ type: ct, data: labels.map((l, i) => ({ name: l, value: values[i] })), label: { fontSize: labelSize } }] }
    if (ct === 'gauge') return { ...base, series: [{ type: 'gauge', detail: { formatter: '{value}', fontSize: labelSize }, data: [{ value: values[0] || 0, name: nameVisible ? xName : '' }] }] }
    if (ct === 'pictorialBar') return { ...base, xAxis: { type: 'category', data: labels, axisLabel }, yAxis: { type: 'value', axisLabel }, series: [{ type: 'pictorialBar', data: values, symbol: 'circle' }] }
    return {
      ...base,
      xAxis: { type: 'category', data: labels, name: nameVisible ? xName : '', axisLabel },
      yAxis: { type: 'value', name: nameVisible ? yName : '', axisLabel },
      series: [{ type: ct === 'line' ? 'line' : 'bar', data: values, itemStyle: { color: colors[0] }, smooth: ct === 'line' }],
    }
  }

  // ---- 布局计算：指标分离、动态行高 ----
  const metricItems = items.filter(i => i.type === 'metric')
  const chartItems = items.filter(i => i.type !== 'metric')
  const metricIds = new Set(metricItems.map(m => m.id))
  const chartLayout = layout.filter(l => !metricIds.has(l.i))

  // 动态行高：根据容器剩余高度和图表总行数计算，确保一屏内无滚动
  const availGridH = Math.max(dashSize.h - headerH - 16, 100)
  const maxRows = chartLayout.length > 0
    ? Math.max(...chartLayout.map(l => l.y + l.h), 4)
    : 4
  const rowHeight = Math.min(Math.max(Math.floor(availGridH / maxRows), 35), 110)

  return (
    <motion.div
      ref={dashboardRef}
      variants={panelVariants} initial="hidden" animate="visible"
      className="dashboard-board"
      style={{
        height: '100%',
        overflow: 'hidden',
        background: 'var(--bg-secondary)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* 顶部控件区域（多屏标签、标题、配色、工具栏、构建器） */}
      <div ref={headerRef} style={{ flexShrink: 0 }}>
        {/* 多屏标签栏 + 新建大屏 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '12px 16px 0', flexWrap: 'wrap' }}>
          {dashboards.map((db, i) => (
            <div key={db.id} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <button onClick={() => setActiveIdx(i)}
                style={{ padding: '4px 10px', fontSize: 13, cursor: 'pointer', borderRadius: 6, border: activeIdx === i ? '2px solid var(--accent)' : '1px solid var(--border-color)', background: activeIdx === i ? 'rgba(138,155,174,0.15)' : 'var(--bg-input)', color: 'var(--text-primary)', fontWeight: activeIdx === i ? 600 : 400 }}>
                {db.name}
              </button>
              {dashboards.length > 1 && activeIdx === i && (
                <span onClick={() => { if (window.confirm('确定删除此大屏及其所有图表？')) delDashboard(i) }} style={{ cursor: 'pointer', color: '#e74c3c', fontSize: 14, padding: '0 2px' }}>×</span>
              )}
            </div>
          ))}
          <button onClick={addDashboard} style={{ padding: '4px 8px', fontSize: 11, cursor: 'pointer', background: 'var(--bg-input)', border: '1px dashed var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>+ 新建大屏</button>
        </div>

        {/* 标题 */}
        <div style={{ textAlign: 'center', padding: '4px 16px 0' }}>
          {renaming ? (
            <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
              <input value={renameVal} onChange={e => setRenameVal(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') doRename() }} autoFocus
                style={{ padding: '4px 10px', fontSize: 18, fontWeight: 600, borderRadius: 6, background: 'var(--bg-input)', border: '1px solid var(--accent)', color: 'var(--text-primary)', outline: 'none', textAlign: 'center', width: 220 }} />
              <button onClick={doRename} style={{ padding: '4px 10px', fontSize: 12, cursor: 'pointer', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 4 }}>✓</button>
            </div>
          ) : (
            <h2 style={{ fontSize: 20, fontWeight: 700, margin: 0, cursor: 'pointer', color: 'var(--text-primary)', letterSpacing: '-0.02em' }}
              onClick={() => { setRenameVal(active.name); setRenaming(true) }}>
              {active.name}
            </h2>
          )}
        </div>

        {/* 统一改色 + 容器透明度 + 撤销栈 */}
        {items.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 16px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>🎨 统一配色:</span>
            <select value={previewColor ?? ''} onChange={e => handleThemeSelect(e.target.value)} style={{ padding: '3px 8px', fontSize: 12, borderRadius: 6, background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)', outline: 'none' }}>
              <option value="">——</option>
              {COLOR_SCHEMES.map((s, i) => (
                <option key={s.name} value={i}>{s.name}</option>
              ))}
            </select>

            <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 4 }}>🔲 透明度:</span>
            <input type="range" min="0" max="100"
              value={Math.round((previewOpacity ?? savedOpacity) * 100)}
              onChange={e => setPreviewOpacity(Number(e.target.value) / 100)}
              style={{ width: 80, cursor: 'pointer', accentColor: 'var(--accent)' }} />
            <span style={{ fontSize: 11, color: 'var(--text-muted)', minWidth: 32 }}>{Math.round((previewOpacity ?? savedOpacity) * 100)}%</span>

            {(previewColor !== null || previewOpacity !== null) && (
              <button onClick={applyTheme} style={{ padding: '3px 12px', fontSize: 11, cursor: 'pointer', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6 }}>
                确定
              </button>
            )}
            {(previewColor !== null || previewOpacity !== null) && (
              <button onClick={cancelPreview} style={{ padding: '3px 12px', fontSize: 11, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>
                取消
              </button>
            )}
            {themeStack.length > 0 && previewColor === null && previewOpacity === null && (
              <button onClick={undoThemeStep} title="从撤销栈恢复上一步配色和透明度设置"
                style={{ padding: '3px 12px', fontSize: 11, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>
                ↩ 恢复上一步{themeStack.length > 1 ? `(${themeStack.length})` : ''}
              </button>
            )}
          </div>
        )}

        {/* 工具栏 */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '4px 16px', flexWrap: 'wrap' }}>
          <button onClick={() => { setBuilderVisible(!builderVisible); setMetricVisible(false) }} style={{
            padding: '4px 12px', fontSize: 12, cursor: 'pointer',
            background: 'var(--bg-input)', border: '1px solid var(--border-color)',
            borderRadius: 6, color: 'var(--text-muted)',
          }}>
            {builderVisible ? '− 收起' : '+ 添加图表'}
          </button>
          <button onClick={() => { setMetricVisible(!metricVisible); setBuilderVisible(false) }} style={{
            padding: '4px 12px', fontSize: 12, cursor: 'pointer',
            background: 'var(--bg-input)', border: '1px solid var(--border-color)',
            borderRadius: 6, color: 'var(--text-muted)',
          }}>
            {metricVisible ? '− 收起' : '+ 添加指标'}
          </button>
          <button onClick={toggleFullscreen} style={{
            padding: '4px 12px', fontSize: 12, cursor: 'pointer',
            background: 'var(--bg-input)', border: '1px solid var(--border-color)',
            borderRadius: 6, color: 'var(--text-muted)',
          }}>
            {isFullscreen ? '⛶ 退出全屏' : '⛶ 全屏'}
          </button>
        </div>

        {/* 图表构建器 */}
        {builderVisible && (
          <div style={{ margin: '4px 16px 8px', padding: 12, background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
            <div><Label>主表</Label>
              <select value={builderForm.table} onChange={e => { setBuilderForm(f => ({ ...f, table: e.target.value, xCol: '', yCol: '' })); loadColumns(e.target.value) }} style={selectStyle}>
                <option value="">选择表</option>
                {tables.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div><Label>X 轴</Label>
              <select value={builderForm.xCol} onChange={e => setBuilderForm(f => ({ ...f, xCol: e.target.value }))} style={selectStyle}>
                <option value="">选择列</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
            <div><Label>Y 轴 / 聚合</Label>
              <select value={builderForm.yCol} onChange={e => setBuilderForm(f => ({ ...f, yCol: e.target.value }))} style={selectStyle}>
                <option value="">选择列</option>
                {columns.map(c => {
                  return <option key={c.name} value={c.name}>{c.name}</option>
                })}
                <option value="COUNT(*)">COUNT(*)</option>
                <option value="SUM(*)">SUM</option>
                <option value="AVG(*)">AVG</option>
              </select>
            </div>
            <div><Label>聚合方式</Label>
              <input value={builderForm.aggFunc} onChange={e => setBuilderForm(f => ({ ...f, aggFunc: e.target.value }))} placeholder="如 SUM, AVG..." style={{ ...smallInputStyle, width: 80 }} />
            </div>
            <div><Label>图表类型</Label>
              <select value={builderForm.chartType} onChange={e => setBuilderForm(f => ({ ...f, chartType: e.target.value }))} style={selectStyle}>
                <option value="bar">柱状图</option>
                <option value="line">折线图</option>
                <option value="pie">饼图</option>
                <option value="scatter">散点图</option>
              </select>
            </div>
            <div><Label>JOIN 表</Label>
              <select value={builderForm.joinTable} onChange={e => setBuilderForm(f => ({ ...f, joinTable: e.target.value }))} style={selectStyle}>
                <option value="">无</option>
                {tables.filter(t => t !== builderForm.table).map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div><Label>JOIN ON</Label>
              <input value={builderForm.joinOn} onChange={e => setBuilderForm(f => ({ ...f, joinOn: e.target.value }))} placeholder="t1.col = t2.col" style={smallInputStyle} />
            </div>
            <div><Label>标题</Label>
              <input value={builderForm.title} onChange={e => setBuilderForm(f => ({ ...f, title: e.target.value }))} placeholder="可选" style={smallInputStyle} />
            </div>
            <button onClick={addCustomChart} disabled={!builderForm.table || !builderForm.xCol || !builderForm.yCol} style={{
              padding: '6px 16px', fontSize: 12, borderRadius: 6, border: 'none',
              background: builderForm.table && builderForm.xCol && builderForm.yCol ? 'var(--accent)' : 'var(--bg-hover)',
              color: builderForm.table && builderForm.xCol && builderForm.yCol ? '#fff' : 'var(--text-muted)',
              cursor: builderForm.table && builderForm.xCol && builderForm.yCol ? 'pointer' : 'not-allowed',
            }}>添加</button>
            <button onClick={() => { setBuilderVisible(false); setBuilderForm({ table: '', xCol: '', yCol: '', chartType: 'bar', title: '', joinTable: '', joinOn: '', aggFunc: '' }) }}
              style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, border: '1px solid var(--border-color)', background: 'var(--bg-input)', color: 'var(--text-muted)', cursor: 'pointer' }}>取消</button>
          </div>
        )}

        {/* 指标构建器 */}
        {metricVisible && (
          <div style={{ margin: '4px 16px 8px', padding: 12, background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
            <div><Label>表</Label>
              <select value={metricForm.table} onChange={e => { setMetricForm(f => ({ ...f, table: e.target.value, column: '' })); loadColumns(e.target.value) }} style={selectStyle}>
                <option value="">选择表</option>
                {tables.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div><Label>列</Label>
              <select value={metricForm.column} onChange={e => setMetricForm(f => ({ ...f, column: e.target.value }))} style={selectStyle}>
                <option value="">*</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
            <div><Label>聚合</Label>
              <select value={metricForm.aggFunc} onChange={e => setMetricForm(f => ({ ...f, aggFunc: e.target.value }))} style={selectStyle}>
                <option value="COUNT">COUNT</option>
                <option value="SUM">SUM</option>
                <option value="AVG">AVG</option>
                <option value="MAX">MAX</option>
                <option value="MIN">MIN</option>
              </select>
            </div>
            <div><Label>过滤条件（可选）</Label>
              <input value={metricForm.filter} onChange={e => setMetricForm(f => ({ ...f, filter: e.target.value }))} placeholder="如 status='已完成'" style={smallInputStyle} />
            </div>
            <div><Label>标题</Label>
              <input value={metricForm.title} onChange={e => setMetricForm(f => ({ ...f, title: e.target.value }))} placeholder="可选" style={smallInputStyle} />
            </div>
            <button onClick={addMetric} disabled={!metricForm.table} style={{
              padding: '6px 16px', fontSize: 12, borderRadius: 6, border: 'none',
              background: metricForm.table ? 'var(--accent)' : 'var(--bg-hover)',
              color: metricForm.table ? '#fff' : 'var(--text-muted)',
              cursor: metricForm.table ? 'pointer' : 'not-allowed',
            }}>添加指标</button>
            <button onClick={() => { setMetricVisible(false); setMetricForm({ table: '', column: '', aggFunc: 'COUNT', filter: '', title: '' }) }}
              style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, border: '1px solid var(--border-color)', background: 'var(--bg-input)', color: 'var(--text-muted)', cursor: 'pointer' }}>取消</button>
          </div>
        )}
      </div>

      {/* KPI 指标栏：单值指标聚合为横向卡片条 */}
      {metricItems.length > 0 && (
        <div className="kpi-metric-card" style={{ display: 'flex', gap: 12, padding: '0 16px 8px', flexShrink: 0 }}>
          {metricItems.map(item => (
            <div key={item.id} style={{
              flex: 1,
              background: 'var(--bg-card)',
              borderRadius: 10,
              padding: '12px 16px',
              display: 'flex',
              flexDirection: 'column',
              position: 'relative',
              boxShadow: 'var(--shadow)',
            }}>
              {/* 删除按钮（悬停显示） */}
              <div className="dashboard-card-actions" style={{
                position: 'absolute', top: 6, right: 8,
                opacity: 0, transition: 'opacity 0.2s ease',
              }}>
                <span onClick={() => removeItem(item.id)} style={{ cursor: 'pointer', color: '#e74c3c', fontSize: 14 }}>✕</span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4, fontWeight: 500 }}>{item.title}</div>
              <div style={{ fontSize: 28, fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.03em' }}>
                {item.metricValue}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 空状态提示 */}
      {items.length === 0 && !builderVisible && !metricVisible && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', fontSize: 14 }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>📊</div>
          <div>数据大屏为空</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>点击上方「+ 添加图表」或「+ 添加指标」创建内容</div>
          <div style={{ fontSize: 12 }}>或在对话中查询后点击「添加到大屏」</div>
        </div>
      )}

      {/* 图表区 Grid（3 列布局，动态行高适配） */}
      {chartItems.length > 0 && (
        <div className="dashboard-grid" style={{ flex: 1, overflow: 'hidden', padding: '0 16px 16px' }}>
          <Suspense fallback={<div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: 40 }}>加载布局引擎...</div>}>
          <GridLayout
            className="layout"
            layout={chartLayout}
            cols={3}
            rowHeight={rowHeight}
            width={Math.max(dashSize.w - 32, 300)}
            onLayoutChange={(newChartLayout) => {
              // 合并指标布局（不在 Grid 中的）与图表新布局
              const metricLayoutEntries = layout.filter(l => metricIds.has(l.i))
              const fullLayout = [...metricLayoutEntries, ...newChartLayout]
              setLayout(fullLayout)
              syncDashboards(fullLayout, items)
            }}
            onResize={onGridResize}
            draggableHandle=".drag-handle"
            isResizable={true}
            compactType="vertical"
            preventCollision={false}
            autoSize={false}
          >
            {chartItems.map(item => {
              // 卡片背景透明度
              const effectiveOpacity = previewOpacity ?? savedOpacity ?? 1
              const bgCardVar = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim()
              const cardBg = effectiveOpacity < 1 ? hexToRgba(bgCardVar || '#252830', effectiveOpacity) : 'var(--bg-card)'

              // 计算高度级别
              const itemLayout = chartLayout.find(l => l.i === item.id)
              const h = itemLayout?.h ?? 4
              const heightLevel = h <= 2 ? 'compact' : h >= 5 ? 'spacious' : 'normal'

              const opt = makeChartOption(item, heightLevel)
              const isEditorOpen = item.chartEditorOpen
              const effectiveColor = previewColor ?? item.manualColor ?? 0

              // 图表高度：减去标题栏和按钮（约 50px）
              const chartHeight = Math.max(rowHeight * h - 50, 60)

              return (
                <div key={item.id} data-echart-id={item.id}
                  className="dashboard-grid-card"
                  style={{
                    background: cardBg,
                    borderRadius: 10,
                    display: 'flex',
                    flexDirection: 'column',
                    position: 'relative',
                    boxShadow: 'var(--shadow)',
                    overflow: 'hidden',
                  }}
                >
                  {/* 标题栏（拖拽手柄） */}
                  <div className="drag-handle" style={{
                    padding: '8px 12px',
                    fontSize: 12,
                    color: 'var(--text-secondary)',
                    cursor: 'grab',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    borderBottom: '1px solid var(--border-color)',
                    userSelect: 'none',
                    flexShrink: 0,
                  }}>
                    <span style={{ fontWeight: 500 }}>{item.title}</span>
                    {/* 操作按钮（悬停显示） */}
                    <div className="dashboard-card-actions" style={{
                      display: 'flex', gap: 6, alignItems: 'center',
                      opacity: 0, transition: 'opacity 0.2s ease',
                    }}>
                      <button onClick={() => setItems(prev => prev.map(it => it.id === item.id ? { ...it, chartEditorOpen: !it.chartEditorOpen } : it))}
                        style={{ padding: '2px 8px', fontSize: 10, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 4, color: 'var(--text-muted)' }}>
                        📊 修改
                      </button>
                      <span onClick={() => removeItem(item.id)} style={{ cursor: 'pointer', color: '#e74c3c', fontSize: 13 }}>✕</span>
                    </div>
                  </div>

                  {/* 图表内容 */}
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
                    {opt ? (
                      <ReactEChartsCore
                        key={item.id + '_' + resizeVer}
                        option={opt}
                        style={{ height: chartHeight, width: '100%' }}
                        opts={{ renderer: 'canvas' }}
                      />
                    ) : item.result ? (
                      <div style={{ color: 'var(--text-primary)', fontSize: 18, fontWeight: 500 }}>
                        {JSON.stringify(item.result[0]?.[Object.keys(item.result[0])[1]] ?? item.result[0]?.[Object.keys(item.result[0])[0]] ?? '—')}
                      </div>
                    ) : (
                      <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>加载中...</div>
                    )}
                  </div>

                  {/* 图表编辑面板（展开时） */}
                  {isEditorOpen && (
                    <div style={{ flexShrink: 0, padding: 8, background: 'var(--bg-secondary)', borderTop: '1px solid var(--border-color)', fontSize: 10 }}>
                      <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
                        <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>图表:</span>
                        <select value={item.chartType || 'bar'} onChange={e => setItems(prev => prev.map(it => it.id === item.id ? { ...it, chartType: e.target.value } : it))} style={{ ...selStyle2, flex: 1 }}>
                          {CHART_TYPES.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}
                        </select>
                      </div>
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 4 }}>
                        {COLOR_SCHEMES.map((s, ci) => (
                          <button key={s.name} onClick={() => setItems(prev => prev.map(it => it.id === item.id ? { ...it, manualColor: ci } : it))}
                            style={{ width: 22, height: 16, borderRadius: 3, cursor: 'pointer', border: effectiveColor === ci ? '2px solid var(--accent)' : '1px solid var(--border-color)', background: `linear-gradient(90deg, ${s.colors.slice(0, 4).join(', ')})`, padding: 0 }} title={s.name} />
                        ))}
                      </div>
                      <button onClick={() => setItems(prev => prev.map(it => it.id === item.id ? { ...it, chartEditorOpen: false } : it))}
                        style={{ padding: '3px 12px', fontSize: 10, cursor: 'pointer', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 4 }}>
                        应用
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </GridLayout>
          </Suspense>
        </div>
      )}

      {/* 删除确认弹窗 */}
      {confirmDeleteId && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 10000, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.4)' }}
          onClick={() => setConfirmDeleteId(null)}>
          <div style={{ background: 'var(--bg-card)', borderRadius: 12, padding: 24, minWidth: 280, boxShadow: '0 8px 32px rgba(0,0,0,0.2)' }}
            onClick={e => e.stopPropagation()}>
            <h3 style={{ fontSize: 15, fontWeight: 500, margin: '0 0 8px', color: 'var(--text-primary)', textAlign: 'center' }}>确认删除</h3>
            <p style={{ fontSize: 13, color: 'var(--text-muted)', textAlign: 'center', margin: '0 0 20px' }}>确定要删除这个图表吗？此操作不可撤销。</p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button onClick={doRemove} style={{ padding: '8px 28px', fontSize: 13, cursor: 'pointer', background: '#e74c3c', color: '#fff', border: 'none', borderRadius: 8, fontWeight: 500 }}>删除</button>
              <button onClick={() => setConfirmDeleteId(null)} style={{ padding: '8px 28px', fontSize: 13, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 8, color: 'var(--text-muted)' }}>取消</button>
            </div>
          </div>
        </div>
      )}
    </motion.div>
  )
}

// TabPanels 导出放在所有函数定义之后，避免 Rolndown CJS→ESM 的 TDZ 问题
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

/* ============================================================================
   系统监控 — 缓存状态 + 请求趋势 + 响应时间 + Token 消耗
   ============================================================================ */
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

  // 模拟请求趋势数据（实际可接入 Prometheus）
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
      <h2 style={{ fontSize: 16, fontWeight: 500, marginBottom: 12 }}>⚙️ 系统监控</h2>

      {/* 缓存指标 */}
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
        {/* 请求趋势 */}
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 12, padding: 16 }}>
          <h3 style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 8 }}>📈 请求趋势（近 7 天）</h3>
          <ReactEChartsCore option={trendOption} style={{ height: 200, width: '100%' }} />
        </div>

        {/* 缓存仪表盘 */}
        {gaugeOption && (
          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 12, padding: 16 }}>
            <h3 style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 8 }}>🎯 缓存命中率</h3>
            <ReactEChartsCore option={gaugeOption} style={{ height: 220, width: '100%' }} />
          </div>
        )}
      </div>

      {/* 扩展指标（有数据时才展示） */}
      {false && ['平均响应时间', '错误率', 'LLM Token 消耗', '活跃 Agent 数'].filter(() => false).length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginTop: 20 }}>
          <div style={{ padding: 14, borderRadius: 12, background: 'var(--bg-card)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
            <div style={{ fontSize: 20, fontWeight: 500, color: 'var(--text-primary)' }}>—</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>占位</div>
          </div>
        </div>
      )}
    </motion.div>
  )
}

/* ============================================================================
   表格信息 — 表列表 + 点击查看 Schema + 数据预览
   ============================================================================ */
function TablesPanel() {
  const [tables, setTables] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedTable, setSelectedTable] = useState(null) // { name, schema, preview }
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
      {/* 左侧表列表 */}
      <div style={{ width: 260, flexShrink: 0 }}>
        <h2 style={{ fontSize: 16, fontWeight: 500, marginBottom: 12 }}>📋 数据库表</h2>
        {loading ? (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>加载中...</p>
        ) : tables.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>请先连接数据库</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {tables.map(t => (
              <button
                key={t}
                onClick={() => loadTableDetail(t)}
                style={{
                  padding: '8px 12px', borderRadius: 8, cursor: 'pointer', textAlign: 'left',
                  background: selectedTable?.name === t ? 'rgba(138,155,174,0.15)' : 'var(--bg-card)',
                  border: selectedTable?.name === t ? '1px solid var(--accent)' : '1px solid var(--border-color)',
                  fontSize: 13, color: 'var(--text-primary)',
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                }}
              >
                <span>📄 {t}</span>
                <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>▸</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 右侧详情 */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {!selectedTable ? (
          <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: 20, textAlign: 'center' }}>
            点击左侧表名查看详情
          </div>
        ) : detailLoading ? (
          <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: 20 }}>加载中...</div>
        ) : (
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 500, marginBottom: 12 }}>📄 {selectedTable.name}</h2>

            {/* Schema */}
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

            {/* 数据预览 */}
            {selectedTable.preview && (
              <div>
                <h3 style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 8 }}>
                  数据预览 (前 {selectedTable.preview.rows.length} 行)
                </h3>
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

/* ============================================================================
   评估报告 — 指标 + 分类图表
   ============================================================================ */
function EvalPanel() {
  const [report, setReport] = useState(null)
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState({ completed: 0, total: 0, current: '' })
  const [results, setResults] = useState([])
  const [usePolling, setUsePolling] = useState(false)
  const esRef = useRef(null)
  const pollRef = useRef(null)

  // 加载已保存的评估报告
  useEffect(() => {
    fetch('/api/eval/report').then(r => r.json()).then(d => { if (!d.error) setReport(d) }).catch(() => {})
  }, [])

  // 清理 SSE/轮询连接（组件卸载或切换面板时不断开）
  const cleanup = () => {
    if (esRef.current) { esRef.current.close(); esRef.current = null }
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  const startEval = () => {
    cleanup()
    setRunning(true)
    setResults([])
    setReport(null)
    setProgress({ completed: 0, total: 0, current: '' })

    // 优先使用 SSE
    try {
      const es = new EventSource('/api/eval/run-stream')
      esRef.current = es

      es.addEventListener('progress', (e) => {
        const d = JSON.parse(e.data)
        setProgress({ completed: d.completed, total: d.total, current: d.current })
      })

      es.addEventListener('result', (e) => {
        const d = JSON.parse(e.data)
        setResults(prev => [...prev, d])
      })

      es.addEventListener('done', (e) => {
        const d = JSON.parse(e.data)
        if (d.report) setReport(d.report)
        setRunning(false)
        cleanup()
      })

      let reconnectAttempted = false
      es.addEventListener('error', (e) => {
        if (es.readyState === EventSource.CLOSED) {
          if (!reconnectAttempted) {
            // 自动重连一次
            reconnectAttempted = true
            es.close()
            setTimeout(() => {
              if (running) {
                try {
                  const es2 = new EventSource('/api/eval/run-stream')
                  esRef.current = es2
                  // 重新绑定事件
                  es2.addEventListener('progress', (ev) => { const d = JSON.parse(ev.data); setProgress({ completed: d.completed, total: d.total, current: d.current }) })
                  es2.addEventListener('result', (ev) => { const d = JSON.parse(ev.data); setResults(prev => [...prev, d]) })
                  es2.addEventListener('done', (ev) => { const d = JSON.parse(ev.data); if (d.report) setReport(d.report); setRunning(false); cleanup() })
                  es2.addEventListener('error', () => { if (es2.readyState === EventSource.CLOSED) { es2.close(); setUsePolling(true); startPolling() } })
                } catch {
                  setUsePolling(true); startPolling()
                }
              }
            }, 2000)
          } else {
            // 重连失败，降级轮询
            es.close()
            setUsePolling(true)
            startPolling()
          }
        }
      })

      // 8 秒超时：如果还没收到任何 progress，SSE 可能失败，降级轮询
      setTimeout(() => {
        if (running && !usePolling && esRef.current?.readyState !== EventSource.OPEN) {
          es.close()
          setUsePolling(true)
          startPolling()
        }
      }, 8000)

    } catch {
      // SSE 不可用，直接降级轮询
      setUsePolling(true)
      startPolling()
    }
  }

  const startPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch('/api/eval/status')
        const d = await r.json()
        if (d.running) {
          setProgress({ completed: d.completed, total: d.total, current: d.current })
          setResults(d.results || [])
        } else {
          setRunning(false)
          setReport(d.report || null)
          if (pollRef.current) clearInterval(pollRef.current)
        }
      } catch {}
    }, 2000)
  }

  // 组件卸载时清理，但切换面板不中断（TabPanels 懒加载保证了活跃时挂载）
  useEffect(() => {
    return () => {
      // 仅在组件真正卸载时清理（SSE 连接由后端超时管理）
      if (esRef.current?.readyState === EventSource.OPEN) {
        // 不关闭 SSE，让后台继续运行，后端超时自动断开
      }
    }
  }, [])

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

  const totalCases = progress.total || report?.overall_metrics?.total_valid || 0
  const completed = progress.completed

  return (
    <motion.div variants={panelVariants} initial="hidden" animate="visible" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h2 style={{ fontSize: 16, fontWeight: 500, margin: 0 }}>📋 评估报告</h2>
        <button onClick={startEval} disabled={running}
          style={{ padding: '4px 14px', fontSize: 12, cursor: running ? 'not-allowed' : 'pointer', background: running ? 'var(--bg-hover)' : 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6 }}>
          {running ? '⏳ 评估中...' : '🚀 开始评估'}
        </button>
      </div>

      {/* 进度条 */}
      {running && totalCases > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
            <span>{progress.current || '准备中...'}</span>
            <span>{completed}/{totalCases} ({totalCases > 0 ? Math.round(completed / totalCases * 100) : 0}%)</span>
          </div>
          <div style={{ height: 6, borderRadius: 3, background: 'var(--bg-hover)', overflow: 'hidden' }}>
            <div style={{ height: '100%', borderRadius: 3, background: 'var(--accent)', width: `${totalCases > 0 ? completed / totalCases * 100 : 0}%`, transition: 'width 0.3s' }} />
          </div>
          {usePolling && <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>⏱ 轮询模式（SSE 不可用）</div>}
        </div>
      )}

      {/* 实时结果列表 */}
      {results.length > 0 && running && (
        <div style={{ marginBottom: 16, maxHeight: 200, overflow: 'auto', background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 8, padding: 8, fontSize: 11 }}>
          <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>实时结果：</div>
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

      {/* 报告内容 */}
      {!report ? (
        !running && <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>尚未运行评估，点击上方「开始评估」按钮</p>
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
