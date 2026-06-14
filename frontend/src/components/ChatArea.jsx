import { useState, useEffect, useRef } from 'react'
import ReactEChartsCore from 'echarts-for-react'
import ChatMascot from './ChatMascot'

/* ==========================================================================
   图表工具函数
   ========================================================================== */

/** 18 种图表类型定义 */
const CHART_TYPES = [
  { key: 'bar', label: '📊 柱状图' },
  { key: 'line', label: '📈 折线图' },
  { key: 'pie', label: '🥧 饼图' },
  { key: 'scatter', label: '🔵 散点图' },
  { key: 'funnel', label: '🔻 漏斗图' },
  { key: 'radar', label: '🕸️ 雷达图' },
  { key: 'heatmap', label: '🔥 热力图' },
  { key: 'treemap', label: '🗂️ 矩形树图' },
  { key: 'sunburst', label: '🌅 旭日图' },
  { key: 'sankey', label: '🔀 桑基图' },
  { key: 'boxplot', label: '📦 箱线图' },
  { key: 'candlestick', label: '🕯️ K线图' },
  { key: 'effectScatter', label: '✨ 特效散点' },
  { key: 'lines', label: '〰️ 轨迹图' },
  { key: 'parallel', label: '📊 平行坐标' },
  { key: 'graph', label: '🔗 关系图' },
  { key: 'gauge', label: '🎯 仪表盘' },
  { key: 'pictorialBar', label: '🎨 象形柱图' },
]

/** 21 种配色方案 */
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

/** 智能推荐图表类型：基于列数和数据类型 */
function autoDetectChartType(cols, data) {
  if (!cols || !data || !data.length) return 'bar'
  const numericCols = cols.filter(c => typeof data[0]?.[c] === 'number')
  const stringCols = cols.filter(c => typeof data[0]?.[c] === 'string')
  const allNumeric = numericCols.length === cols.length
  const rowCount = data.length

  if (rowCount <= 1) return 'bar'
  if (cols.length === 2 && stringCols.length === 1 && numericCols.length === 1 && rowCount <= 6 && rowCount > 1) return 'pie'
  if (cols.length >= 3 && numericCols.length >= 2) return 'scatter'
  if (rowCount > 15 && cols.length >= 2 && numericCols.length >= 1) return 'line'
  if (cols.length >= 3 && numericCols.length >= 2 && rowCount > 5) return 'scatter'
  if (allNumeric && cols.length === 1) return 'bar'
  return 'bar'
}

/** 智能推荐 X/Y 轴 */
function autoDetectXY(cols, data) {
  if (!cols || !data || !data.length) return { xCol: cols[0], yCol: cols[1] || cols[0] }
  const numericCols = cols.filter(c => typeof data[0]?.[c] === 'number')
  const stringCols = cols.filter(c => typeof data[0]?.[c] === 'string')
  // 优先非数值列作为 X 轴，数值列作为 Y 轴
  let xCol = stringCols[0] || cols[0]
  let yCol = numericCols[0] || cols.find(c => c !== xCol) || numericCols[0] || xCol
  return { xCol, yCol }
}

// 简易文本相似度
function textSimilarity(a, b) {
  const shorter = a.length < b.length ? a : b
  const longer = a.length < b.length ? b : a
  if (longer.length === 0) return 1.0
  let matches = 0
  for (let i = 0; i < shorter.length; i++) { if (longer.includes(shorter[i])) matches++ }
  return matches / shorter.length
}

function loadMessages(convId) {
  // 优先加载当前对话的独立消息存档，降级到通用 chat_messages
  const key = convId ? 'chat_messages_' + convId : 'chat_messages'
  try {
    const saved = localStorage.getItem(key)
    if (saved) return JSON.parse(saved)
    // 降级：如果是通用键但没有 convId，尝试有 convId 的存档
    if (!convId) {
      const history = JSON.parse(localStorage.getItem('chat_history') || '[]')
      if (history.length > 0) {
        const last = history[history.length - 1]
        const fallback = localStorage.getItem('chat_messages_' + last.id)
        if (fallback) return JSON.parse(fallback)
      }
    }
  } catch {}
  return [{ role: 'assistant', content: '你好！我是你的数据分析助手。请输入自然语言问题，我会帮你生成 SQL 并查询数据。' }]
}

function saveToHistory(q, msg, messages, convId) {
  try {
    const history = JSON.parse(localStorage.getItem('chat_history') || '[]')
    const title = shortTitle(q)

    // ---- 历史去重 ----
    // 检查最后 5 条历史中是否有相似问题
    const recent = history.slice(-5)
    let deduped = false
    for (const entry of recent) {
      if (!entry.question) continue
      const sim = textSimilarity(q, entry.question)
      if (sim > 0.85) {
        // 相似问题：保留结果更好（有数据 > 行数多 > 耗时近）的条目
        const currentRows = msg.result?.length || 0
        const existingRows = entry.resultCount || 0
        if (currentRows >= existingRows && msg.sql) {
          // 当前结果更好，更新条目
          entry.question = q
          entry.preview = title
          entry.sql = msg.sql || ''
          entry.resultSummary = msg.result ? msg.result.length + ' 条结果' : ''
          entry.resultCount = currentRows
        }
        // 保留更优结果后标记已去重
        deduped = true
        break
      }
    }

    // 只在非去重时才更新最后一条
    if (!deduped && history.length > 0) {
      const last = history[history.length - 1]
      last.question = q
      last.preview = title
      last.sql = msg.sql || ''
      last.resultSummary = msg.result ? msg.result.length + ' 条结果' : ''
      last.resultCount = msg.result?.length || 0
    }
    if (!deduped) {
      localStorage.setItem('chat_history', JSON.stringify(history))
    }

    // 保存完整消息到 chat_messages_{convId}（会话隔离）
    if (messages && messages.length > 0) {
      if (convId) {
        try { localStorage.setItem('chat_messages_' + convId, JSON.stringify(messages)) } catch {}
      } else if (history.length > 0) {
        const last = history[history.length - 1]
        try { localStorage.setItem('chat_messages_' + last.id, JSON.stringify(messages)) } catch {}
      }
      // 同时保存到通用键作为降级
      try { localStorage.setItem('chat_messages', JSON.stringify(messages)) } catch {}
    }
    window.dispatchEvent(new Event('chat-history-changed'))
  } catch {}
}

/** 从用户问题中提取短标题 */
function shortTitle(q) {
  if (!q) return '查询结果'
  let t = q.replace(/^(查询|统计|计算|找出|列出|显示|告诉我|看看|帮我)/, '').trim()
  t = t.replace(/[。，、！？\s]+$/, '').trim()
  return t.length > 15 ? t.slice(0, 15) + '...' : t || '查询结果'
}

let _globalSessionId = 0

export default function ChatArea(props) {
  const [input, setInput] = useState('')
  // ---- 消息状态改为 conversations 映射（按对话 ID 隔离）----
  const [conversations, setConversations] = useState(() => {
    const initId = props.convId
    const saved = loadMessages(initId)
    return { [initId]: saved }
  })
  // 当前 UI 显示的对话 ID
  const [activeConvId, setActiveConvId] = useState(props.convId)
  // 当前正在 SSE 流式输出的对话 ID（ref，不触发渲染）
  const streamingConvIdRef = useRef(null)
  const sessionIdRef = useRef(0)
  const [suggestions, setSuggestions] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadingStep, setLoadingStep] = useState('')
  const [restoreLoading, setRestoreLoading] = useState(false)
  const [editingIdx, setEditingIdx] = useState(null)
  const [confirmModal, setConfirmModal] = useState(null) // { msg, i, targetDashboard? } 添加到大屏确认弹窗
  const [dashboardList, setDashboardList] = useState([])
  const [selectedDashboard, setSelectedDashboard] = useState('')
  const [detectedType, setDetectedType] = useState('chart') // 'metric' | 'chart' 自动检测结果
  const [detectingType, setDetectingType] = useState(false) // 是否正在检测中
  const [manualType, setManualType] = useState(null) // null=使用自动检测, 'metric'|'chart'=手动覆盖
  const [thinkingMode, setThinkingMode] = useState(() => {
    try { return localStorage.getItem('thinking_mode') || 'normal' } catch { return 'normal' }
  })
  const [mascotStatus, setMascotStatus] = useState('idle') // 'idle' | 'thinking'
  const chatAreaRef = useRef(null)
  const loadingBubbleRef = useRef(null)
  const loadingSteps = ['🔄 分析查询意图...', '📋 检索数据库结构...', '📝 生成 SQL...', '⚡ 执行查询...']

  // 持久化思考模式偏好
  useEffect(() => {
    try { localStorage.setItem('thinking_mode', thinkingMode) } catch {}
  }, [thinkingMode])

  // 加载大屏列表（从 dashboard_manager localStorage）
  useEffect(() => {
    try {
      const mgr = JSON.parse(localStorage.getItem('dashboard_manager') || '{}')
      const screens = Object.keys(mgr).filter(k => k !== 'current')
      setDashboardList(screens)
      if (screens.length > 0) setSelectedDashboard(screens[0])
    } catch {}
  }, [])
  const msgEndRef = useRef(null)

  // 持久化所有对话消息到 localStorage（每个对话独立键名）
  useEffect(() => {
    Object.entries(conversations).forEach(([cid, msgs]) => {
      try { localStorage.setItem('chat_messages_' + cid, JSON.stringify(msgs)) } catch {}
    })
  }, [conversations])

  useEffect(() => {
    let c = false
    fetch('/api/db/suggest-questions').then(r => r.json()).then(d => { if (!c && d.questions?.length) setSuggestions(d.questions) }).catch(() => {})
    return () => { c = true }
  }, [])

  const refreshSuggestions = async () => {
    try {
      const r = await fetch('/api/db/suggest-questions?refresh=' + Date.now())
      const d = await r.json()
      if (d.questions?.length) setSuggestions(d.questions)
    } catch {}
  }

  // ---- 对话切换（来自历史侧栏） + 新建对话 ----
  useEffect(() => {
    const onSwitchConv = (e) => {
      const { convId, question, messages: msgs } = e.detail || {}
      if (!convId) return
      setConversations(prev => {
        if (prev[convId]) return prev // 已加载
        return { ...prev, [convId]: msgs || [] }
      })
      setActiveConvId(convId)
      if (question) setInput(question)
    }
    const onNewChat = (e) => {
      const cid = e.detail?.convId
      if (!cid) return
      setConversations(prev => {
        if (prev[cid]) return prev
        return { ...prev, [cid]: [{ role: 'assistant', content: '你好！我是你的数据分析助手。请输入自然语言问题，我会帮你生成 SQL 并查询数据。' }] }
      })
      setActiveConvId(cid)
      setInput('')
    }
    const onRestoreLoading = (e) => { setRestoreLoading(e.detail?.loading ?? false) }

    window.addEventListener('switch-conversation', onSwitchConv)
    window.addEventListener('new-chat', onNewChat)
    window.addEventListener('restore-loading', onRestoreLoading)
    return () => {
      window.removeEventListener('switch-conversation', onSwitchConv)
      window.removeEventListener('new-chat', onNewChat)
      window.removeEventListener('restore-loading', onRestoreLoading)
    }
  }, [])

  // 确认弹窗打开时，自动调用后端检测类型（指标/图表）
  useEffect(() => {
    if (!confirmModal) return
    const msg = confirmModal.msg
    setDetectingType(true)
    setManualType(null)
    setDetectedType('chart')

    if (!msg.sql) {
      // 无 SQL 时降级为结果形状判断
      const fallback = (!msg.result || msg.result.length === 0) ? 'chart' :
        (msg.result.length === 1 && Object.keys(msg.result[0]).length === 1) ? 'metric' : 'chart'
      setDetectedType(fallback)
      setDetectingType(false)
      return
    }

    fetch('/api/dashboard/detect-type', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql: msg.sql, result: msg.result }),
    })
      .then(r => r.json())
      .then(d => {
        setDetectedType(d.type || 'chart')
        setDetectingType(false)
      })
      .catch(() => {
        // 网络异常时降级为结果形状判断
        const fallback = (!msg.result || msg.result.length === 0) ? 'chart' :
          (msg.result.length === 1 && Object.keys(msg.result[0]).length === 1) ? 'metric' : 'chart'
        setDetectedType(fallback)
        setDetectingType(false)
      })
  }, [confirmModal])

  useEffect(() => { msgEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [conversations, activeConvId])

  // ---- 小猫公仔位置计算 ----
  // idle 时固定在输入区右上，thinking 时定位到最新 AI 气泡上方
  const [mascotPos, setMascotPos] = useState({ top: 'auto', left: 'auto', right: 28, bottom: 76 })
  useEffect(() => {
    if (mascotStatus === 'thinking' && loadingBubbleRef.current && chatAreaRef.current) {
      const bubbleEl = loadingBubbleRef.current
      const chatEl = chatAreaRef.current
      const chatRect = chatEl.getBoundingClientRect()
      const bubbleRect = bubbleEl.getBoundingClientRect()
      setMascotPos({
        top: bubbleRect.top - chatRect.top - 50,
        left: Math.max(40, bubbleRect.left - chatRect.left + 20),
        right: 'auto',
        bottom: 'auto',
      })
    } else if (mascotStatus === 'idle') {
      setMascotPos({ top: 'auto', left: 'auto', right: 28, bottom: 76 })
    }
  }, [mascotStatus, loading])

  // 广播当前活跃对话 ID 给侧栏用于高亮
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('conv-active', { detail: { id: activeConvId || null } }))
  })

  const handleSend = async () => {
    if (!input.trim() || loading) return
    const q = input.trim(); setInput('')
    const cid = activeConvId // 当前对话 ID，SSE 流全程绑定此值
    streamingConvIdRef.current = cid

    // 追加用户消息到 conversations[cid]
    setConversations(prev => ({
      ...prev,
      [cid]: [...(prev[cid] || []), { role: 'user', content: q }]
    }))
    setLoading(true); setLoadingStep('🔄 分析查询意图...')
    setMascotStatus('thinking')

    try {
      // 使用 SSE 流式端点（实时进度更新）
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, history: [], conv_id: cid, thinking_mode: thinkingMode }),
      })
      const reader = res.body?.getReader()
      if (!reader) throw new Error('无法读取响应流')

      const decoder = new TextDecoder()
      let buffer = ''
      let resultData = null
      let sqlText = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // 解析 SSE 事件
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''
        for (const block of parts) {
          const lines = block.split('\n')
          let eventType = '', dataStr = ''
          for (const l of lines) {
            if (l.startsWith('event: ')) eventType = l.slice(7)
            if (l.startsWith('data: ')) dataStr = l.slice(6)
          }
          if (!dataStr) continue
          try {
            const d = JSON.parse(dataStr)
            // ---- 关键修复：校验 cid 是否匹配当前流 ----
            if (d.cid && d.cid !== streamingConvIdRef.current) continue

            if (eventType === 'step') {
              setLoadingStep(d.message || '处理中...')
            } else if (eventType === 'sql') {
              sqlText = d.sql || ''
            } else if (eventType === 'result') {
              resultData = d
            } else if (eventType === 'error') {
              resultData = { error: d.error || '请求出错' }
            }
          } catch {}
        }
      }

      // 如果用户在此期间切换了对话（streamingConvIdRef 发生变化），丢弃此响应
      if (streamingConvIdRef.current !== cid) return

      const data = resultData || { error: '未收到有效响应' }
      // LLM 智能推荐初始图表（调用 /api/chart/recommend，失败降级到规则）
      let recommendedType = 'bar'; let rx, ry, rSeries, rStacked
      if (data.sql && data.result?.length > 0 && data.columns?.length > 1) {
        try {
          const rows = data.result.slice(0, 3).map(r => data.columns.map(c => r[c]))
          const recRes = await fetch('/api/chart/recommend', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              question: q,
              history: [{ columns: data.columns, rows }],
            }),
          })
          const rec = await recRes.json()
          if (rec.chartType) {
            recommendedType = rec.chartType
            rx = rec.xAxis
            ry = rec.yAxis
            rSeries = rec.seriesField
            rStacked = rec.stacked
          }
        } catch {
          recommendedType = autoDetectChartType(data.columns, data.result)
          const xy = autoDetectXY(data.columns, data.result); rx = xy.xCol; ry = xy.yCol
        }
      }
      // 生成智能标题
      const chartTypeLabel = CHART_TYPES.find(t => t.key === recommendedType)?.label || recommendedType
      const shortT = shortTitle(q)
      let smartTitle = shortT
      if (data.sql && data.columns?.length > 0 && recommendedType) {
        smartTitle = shortT + (rx ? ` — 按${rx}` : '') + ` — ${chartTypeLabel}`
      }
      const title = smartTitle
      const msg = { role: 'assistant', content: '', ...data, title, userQuestion: q, thinkingOpen: false, chartType: recommendedType, analysis: null, analysisLoading: false, manualX: rx, manualY: ry, seriesField: rSeries || '', stacked: rStacked || false, manualColor: 0, chartEditorOpen: false }
      if (data.error) {
        const err = data.error
        if (err.includes('分类器') || err.includes('router') || err.includes('意图')) msg.content = '🤔 暂时无法理解您的问题，请换个说法试试。'
        else msg.content = '❌ ' + err
      }
      else if (data.sql) {
        const rowCount = Array.isArray(data.result) ? data.result.length : 0
        msg.content = rowCount > 0 ? `查询完成，共 ${rowCount} 条结果` : '✅ 查询完成（无匹配数据）'
        msg.result = data.result || []; msg.columns = data.columns || []
      } else msg.content = '✅ 处理完成'

      // 追加助手回复到 conversations[cid]（数据源隔离）
      setConversations(prev => {
        const conv = prev[cid] || []
        const updated = [...conv, msg]
        if (!data.error) saveToHistory(q, msg, updated, cid)
        return { ...prev, [cid]: updated }
      })
      window.dispatchEvent(new Event('chat-history-changed'))
    } catch (e) {
      setConversations(prev => ({
        ...prev,
        [cid]: [...(prev[cid] || []), { role: 'assistant', content: '❌ 请求失败: ' + e.message }]
      }))
    }
    finally { setLoading(false); setLoadingStep(''); setMascotStatus('idle'); streamingConvIdRef.current = null }
  }

  const toggleThinking = (i) => setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, thinkingOpen: !m.thinkingOpen } : m))

  const doExplainChart = async (i) => {
    const msg = messages[i]
    setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, analysisLoading: true } : m))
    try {
      const res = await fetch('/api/chat/explain-chart', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: msg.sql?.slice(0, 100) || '图表分析', history: [{ sql: msg.sql, result: msg.result, columns: msg.columns }] }) })
      const data = await res.json()
      setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, analysis: data.analysis, analysisLoading: false } : m))
    } catch { setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, analysis: '分析请求失败', analysisLoading: false } : m)) }
  }

  const addToDashboard = (msg, targetDashboard, itemType) => {
    const cols = msg.columns || []
    const data = msg.result || []
    let chartData = null
    let metricValue = null

    // 若为指标类型，从结果中提取单值
    if (itemType === 'metric' && data.length > 0 && cols.length > 0) {
      const row = data[0]
      const valCol = cols[0]
      metricValue = row[valCol] !== undefined && row[valCol] !== null ? String(row[valCol]) : '—'
    }

    // 图表类型：构建 chartData
    if (data.length > 0 && cols.length >= 2) {
      const xCol = msg.manualX || cols.find(c => typeof data[0]?.[c] !== 'number') || cols[0]
      const yCol = msg.manualY || cols.find(c => c !== xCol && typeof data[0]?.[c] === 'number') || cols.find(c => c !== xCol) || cols[0]
      if (xCol && yCol) {
        chartData = {
          labels: data.map(r => r[xCol]),
          values: data.map(r => Number(r[yCol]) || 0),
        }
      }
    }
    const payload = {
      title: msg.title || shortTitle(msg.userQuestion || '') || msg.sql?.slice(0, 30) + '...' || '查询结果',
      sql: msg.sql,
      result: data,
      columns: cols,
      type: itemType || 'chart',              // 自动识别的类型: 'metric' | 'chart'
      metricValue,                             // 指标卡数值（仅 type=metric 时有值）
      chartData,
      chartType: msg.chartType || 'bar',
      xCol: msg.manualX || '',
      yCol: msg.manualY || '',
      seriesField: msg.seriesField || '',
      stacked: msg.stacked || false,
      manualColor: msg.manualColor ?? 0,
      targetDashboard: targetDashboard || '', // 目标大屏名称
    }
    // 带目标大屏信息写入 localStorage
    try { localStorage.setItem('pending_dashboard_item', JSON.stringify(payload)) } catch {}
    window.dispatchEvent(new CustomEvent('add-to-dashboard', { detail: payload }))
  }

  const applyChartEdit = (i) => { setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, chartEditorOpen: false } : m)); setEditingIdx(null) }

  /** 构建 ECharts option */
  const buildChartOption = (msg) => {
    if (!msg.result || !msg.result.length || !msg.columns?.length) return null
    const cols = msg.columns; const data = msg.result
    const colors = COLOR_SCHEMES[msg.manualColor ?? 0]?.colors || COLOR_SCHEMES[0].colors
    const ct = msg.chartType || 'bar'
    const axisLabel = { color: '#8892a8', rotate: data.length > 8 ? 40 : 0 }

    // 由 LLM /api/chart/recommend 返回的轴配置，用户也可手动修改
    const xCol = msg.manualX || cols.find(c => typeof data[0]?.[c] !== 'number') || cols[0]
    const yCol = msg.manualY || cols.find(c => c !== xCol && typeof data[0]?.[c] === 'number') || cols.find(c => c !== xCol) || cols[0]

    if (!xCol || !yCol) return null
    const labels = data.map(r => String(r[xCol] ?? ''))
    const base = { tooltip: { trigger: ct === 'pie' ? 'item' : 'axis' }, color: colors }

    // 标准柱/折线
    const values = data.map(r => Number(r[yCol]) || 0)
    const grid = { left: 55, right: 25, top: 25, bottom: 55 }

    if (ct === 'pie') return { ...base, series: [{ type: 'pie', data: labels.map((l, i) => ({ name: l, value: values[i] })), label: { color: '#8892a8' } }] }
    if (ct === 'scatter' || ct === 'effectScatter') return { ...base, xAxis: { type: 'value' }, yAxis: { type: 'value' }, series: [{ type: ct, data: data.map(r => [Number(r[xCol]) || 0, Number(r[yCol]) || 0]) }] }
    if (ct === 'funnel') return { ...base, series: [{ type: 'funnel', data: labels.map((l, i) => ({ name: l, value: values[i] })) }] }
    if (ct === 'radar') return { ...base, radar: { indicator: labels.map(l => ({ name: l })) }, series: [{ type: 'radar', data: [{ value: values }] }] }
    if (ct === 'heatmap') return { ...base, xAxis: { type: 'category', data: labels, axisLabel }, yAxis: { type: 'category', data: ['value'], axisLabel }, visualMap: { min: Math.min(...values), max: Math.max(...values) }, series: [{ type: 'heatmap', data: labels.map((l, i) => [i, 0, values[i]]) }] }
    if (ct === 'treemap' || ct === 'sunburst') return { ...base, series: [{ type: ct, data: labels.map((l, i) => ({ name: l, value: values[i] })) }] }
    if (ct === 'gauge') return { ...base, series: [{ type: 'gauge', detail: { formatter: '{value}' }, data: [{ value: values[0] || 0, name: xCol }] }] }
    if (ct === 'pictorialBar') return { ...base, xAxis: { type: 'category', data: labels, axisLabel }, yAxis: { type: 'value', axisLabel }, series: [{ type: 'pictorialBar', data: values, symbol: 'circle' }] }

    return { ...base, grid, xAxis: { type: 'category', data: labels, name: xCol, axisLabel }, yAxis: { type: 'value', name: yCol, axisLabel }, series: [{ type: ct === 'line' ? 'line' : 'bar', data: values, itemStyle: { color: colors[0] }, smooth: ct === 'line' }] }
  }

  return (
    <div ref={chatAreaRef} style={{ display: 'flex', flexDirection: 'column', height: '100%', position: 'relative' }}>
      <div style={{ flex: 1, overflow: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {(conversations[activeConvId] || []).map((msg, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexDirection: msg.role === 'user' ? 'row-reverse' : 'row' }}>
            <div style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, background: msg.role === 'user' ? 'var(--accent)' : 'var(--bg-hover)', color: msg.role === 'user' ? '#fff' : 'var(--text-muted)' }}>
              {msg.role === 'user' ? '🐱' : '🤖'}
            </div>

            <div style={{ maxWidth: '80%', minWidth: 200, padding: '10px 14px', borderRadius: 14, fontSize: 13, lineHeight: 1.6, wordBreak: 'break-word', ...(msg.role === 'user' ? { background: 'var(--bg-hover)', color: 'var(--text-primary)', borderBottomRightRadius: 4 } : { background: 'var(--bg-card)', color: 'var(--text-primary)', border: '1px solid var(--border-color)', borderBottomLeftRadius: 4, boxShadow: 'var(--shadow)' }) }}>
              {msg.content}

              {/* 思维链 — 有内容时才渲染按钮 */}
              {msg.thinking && msg.thinking.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <button onClick={() => setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, thinkingOpen: !m.thinkingOpen } : m))}
                    style={{ padding: '2px 8px', fontSize: 11, cursor: 'pointer', border: 'none', background: 'var(--bg-input)', borderRadius: 4, color: 'var(--text-muted)' }}>
                    {msg.thinkingOpen ? '▼ 收起分析过程' : '▶ 展开分析过程'}
                  </button>
                  {msg.thinkingOpen && <div style={{ marginTop: 6, padding: 8, fontSize: 11, lineHeight: 1.5, background: 'var(--bg-secondary)', borderRadius: 6, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{msg.thinking}</div>}
                </div>
              )}

              {/* SQL */}
              {msg.sql && <pre style={{ marginTop: 8, padding: 10, borderRadius: 8, background: 'var(--bg-secondary)', fontSize: 11, overflowX: 'auto', border: '1px solid var(--border-color)' }}>{msg.sql}</pre>}

              {/* 图表 */}
              {msg.result && msg.result.length > 0 && msg.columns?.length > 1 && (
                <div style={{ marginTop: 8, width: '100%', overflow: 'hidden' }}>
                  {/* 推荐图表类型展示（仅当前推荐类型，无多个选项） */}
                  <div style={{ display: 'flex', gap: 4, marginBottom: 6, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)', padding: '2px 0' }}>📊 {CHART_TYPES.find(t => t.key === msg.chartType)?.label || msg.chartType}</span>
                  </div>

                  {/* 图表（自适应容器） */}
                  <div style={{ width: '100%', aspectRatio: '16/9', maxHeight: 300 }}>
                    {buildChartOption(msg) && <ReactEChartsCore key={msg.chartType + (msg.manualX || '') + (msg.manualY || '') + (msg.manualColor ?? 0)} option={buildChartOption(msg)} style={{ height: '100%', width: '100%' }} opts={{ renderer: 'canvas' }} />}
                  </div>

                  {/* 操作按钮行 */}
                  <div style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>
                    <button onClick={() => { setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, chartEditorOpen: !m.chartEditorOpen } : m)); setEditingIdx(i) }}
                      style={{ padding: '3px 10px', fontSize: 11, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>
                      📊 修改图表
                    </button>
                    {!msg.analysisLoading && !msg.analysis && (
                      <button onClick={() => doExplainChart(i)} style={{ padding: '3px 10px', fontSize: 11, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>📝 图表解析</button>
                    )}
                    <button onClick={() => setConfirmModal({ msg, i })}
                      style={{ padding: '3px 10px', fontSize: 11, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 6, color: 'var(--text-muted)' }}>📊 添加到大屏</button>
                  </div>
                  {msg.analysisLoading && <div style={{ marginTop: 4, fontSize: 11, color: 'var(--text-muted)' }}>🤔 正在分析图表...</div>}
                  {msg.analysis && (
                    <div style={{ marginTop: 4 }}>
                      <button onClick={() => setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, analysisOpen: !m.analysisOpen } : m))}
                        style={{ padding: '2px 8px', fontSize: 11, cursor: 'pointer', border: 'none', background: 'var(--bg-input)', borderRadius: 4, color: 'var(--text-muted)' }}>
                        {msg.analysisOpen ? '▼ 收起图表解析' : '▶ 展开图表解析'}
                      </button>
                      {msg.analysisOpen && <div style={{ marginTop: 4, padding: 8, fontSize: 11, lineHeight: 1.5, background: 'var(--bg-secondary)', borderRadius: 6, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{msg.analysis}</div>}
                    </div>
                  )}

                  {/* 图表编辑面板 — 选择变更后图表即时更新 */}
                  {msg.chartEditorOpen && (
                    <div style={{ marginTop: 8, padding: 10, background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 8, fontSize: 11 }}>
                      {/* 横轴列选择 */}
                      <div style={{ marginBottom: 6 }}>
                        <span style={{ color: 'var(--text-muted)', marginRight: 4 }}>横轴:</span>
                        <select value={msg.manualX || msg.columns[0] || ''} onChange={e => { setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, manualX: e.target.value } : m)) }} style={selStyle}>
                          {msg.columns.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </div>
                      {/* 纵轴列选择 */}
                      <div style={{ marginBottom: 6 }}>
                        <span style={{ color: 'var(--text-muted)', marginRight: 4 }}>纵轴:</span>
                        <select value={msg.manualY || (msg.columns.length > 1 ? msg.columns[1] : msg.columns[0]) || ''} onChange={e => { setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, manualY: e.target.value } : m)) }} style={selStyle}>
                          {msg.columns.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </div>
                      {/* 图表类型选择（两行网格） */}
                      <div style={{ marginBottom: 6 }}>
                        <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>图表类型:</div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 3 }}>
                          {CHART_TYPES.map(t => (
                            <button key={t.key} onClick={() => setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, chartType: t.key } : m))}
                              style={{ padding: '3px 4px', fontSize: 10, cursor: 'pointer', borderRadius: 4, background: msg.chartType === t.key ? 'rgba(138,155,174,0.2)' : 'var(--bg-input)', border: msg.chartType === t.key ? '1px solid var(--accent)' : '1px solid var(--border-color)', color: 'var(--text-primary)' }}>
                              {t.label}
                            </button>
                          ))}
                        </div>
                      </div>
                      {/* 颜色方案选择 */}
                      <div style={{ marginBottom: 6 }}>
                        <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>配色:</div>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                          {COLOR_SCHEMES.map((s, ci) => (
                            <button key={s.name} onClick={() => setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, manualColor: ci } : m))}
                              style={{ width: 28, height: 20, borderRadius: 4, cursor: 'pointer', border: msg.manualColor === ci ? '2px solid var(--accent)' : '1px solid var(--border-color)', background: `linear-gradient(90deg, ${s.colors.slice(0, 4).join(', ')})`, padding: 0 }} title={s.name} />
                          ))}
                        </div>
                      </div>
                      {/* 应用按钮 */}
                      <button onClick={() => { setMessages(prev => prev.map((m, idx) => idx === i ? { ...m, chartEditorOpen: false } : m)); setEditingIdx(null) }}
                        style={{ padding: '4px 16px', fontSize: 11, cursor: 'pointer', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6 }}>
                        应用
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
        {/* 加载态智能体气泡 — 显示状态步骤 */}
        {loading && (
          <div ref={loadingBubbleRef} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <div style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, background: 'var(--bg-hover)', color: 'var(--text-muted)' }}>🤖</div>
            <div style={{ maxWidth: '75%', padding: '10px 14px', borderRadius: 14, fontSize: 13, background: 'var(--bg-card)', color: 'var(--text-primary)', border: '1px solid var(--border-color)', borderBottomLeftRadius: 4, boxShadow: 'var(--shadow)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{loadingStep}</span>
                <span style={{ display: 'inline-block', width: 12, height: 12, border: '2px solid var(--text-muted)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }}></span>
              </div>
            </div>
          </div>
        )}
        {restoreLoading && <div style={{ textAlign: 'center', padding: 12 }}><span style={{ display: 'inline-block', padding: '6px 16px', fontSize: 12, background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 20, color: 'var(--text-muted)', animation: 'pulse 1.5s ease-in-out infinite' }}>⏳ 正在恢复对话...</span></div>}
        <style>{restoreLoading ? '@keyframes pulse { 0%,100%{opacity:0.4} 50%{opacity:1} }' : ''}</style>
        <div ref={msgEndRef} />
      </div>

      {suggestions.length > 0 && (
        <div style={{ padding: '6px 16px', display: 'flex', gap: 6, flexWrap: 'wrap', borderTop: '1px solid var(--border-color)', background: 'var(--bg-secondary)', flexShrink: 0, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>💡</span>
          {suggestions.map(q => (
            <button key={q} onClick={() => setInput(q)} style={{ padding: '3px 12px', fontSize: 11, background: '#fff', color: 'var(--accent)', border: '1px solid var(--accent)', borderRadius: 16, cursor: 'pointer' }}>{q}</button>
          ))}
          <button onClick={refreshSuggestions} title="换一批" style={{ padding: '2px 8px', fontSize: 11, background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}>🔄 换一批</button>
        </div>
      )}

      <div style={{ padding: '12px 16px', display: 'flex', gap: 8, background: 'var(--bg-primary)', alignItems: 'center' }}>
        <button onClick={() => setThinkingMode(prev => prev === 'normal' ? 'deep' : 'normal')}
          title={thinkingMode === 'deep' ? '深度思考模式' : '普通模式'}
          style={{ padding: '6px 10px', borderRadius: 8, fontSize: 12, whiteSpace: 'nowrap',
            background: thinkingMode === 'deep' ? 'var(--accent)' : 'var(--bg-input)',
            color: thinkingMode === 'deep' ? '#fff' : 'var(--text-muted)',
            border: '1px solid var(--border-color)', cursor: 'pointer' }}>
          {thinkingMode === 'deep' ? '🧠 深度' : '⚡ 普通'}
        </button>
        <textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }} placeholder="输入问题，例如：广东销售额" rows={1} disabled={loading}
          style={{ flex: 1, resize: 'none', padding: '8px 12px', fontSize: 14, border: '1px solid var(--border-color)', borderRadius: 10, background: 'var(--bg-input)', color: 'var(--text-primary)', outline: 'none', minHeight: 40, maxHeight: 120 }} />
        <button onClick={handleSend} disabled={!input.trim() || loading}
          style={{ padding: '0 20px', borderRadius: 10, background: input.trim() && !loading ? 'var(--accent)' : 'var(--bg-hover)', color: input.trim() && !loading ? '#fff' : 'var(--text-muted)', border: 'none', cursor: input.trim() && !loading ? 'pointer' : 'not-allowed', fontSize: 13, fontWeight: 500 }}>
          发送
        </button>
      </div>

      {/* 像素小猫公仔 — 思考时在气泡上跑跳，空闲时在输入区右侧 */}
      <ChatMascot status={mascotStatus} style={{ top: mascotPos.top, left: mascotPos.left, right: mascotPos.right, bottom: mascotPos.bottom }} />

      {/* 添加到大屏确认弹窗（含类型自动识别 + 手动切换） */}
      {confirmModal && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 10000, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.4)' }}
          onClick={() => setConfirmModal(null)}>
          <div style={{ background: 'var(--bg-card)', borderRadius: 12, padding: 24, minWidth: 360, maxWidth: 460, boxShadow: '0 8px 32px rgba(0,0,0,0.2)' }}
            onClick={e => e.stopPropagation()}>
            <h3 style={{ fontSize: 15, fontWeight: 500, margin: '0 0 12px', color: 'var(--text-primary)', textAlign: 'center' }}>📊 添加到大屏</h3>
            {confirmModal.msg.sql && (
              <pre style={{ fontSize: 11, padding: 8, background: 'var(--bg-secondary)', borderRadius: 6, maxHeight: 60, overflow: 'auto', margin: '0 0 12px', color: 'var(--text-secondary)' }}>{confirmModal.msg.sql}</pre>
            )}

            {/* 类型自动识别区域 */}
            <div style={{ marginBottom: 14, padding: '10px 12px', background: 'var(--bg-secondary)', borderRadius: 8, border: '1px solid var(--border-color)' }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>
                {detectingType ? '🔍 正在识别类型...' : '🎯 类型识别结果'}
              </div>
              {!detectingType && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{
                    fontSize: 12, fontWeight: 500,
                    padding: '2px 10px', borderRadius: 12,
                    background: (manualType || detectedType) === 'metric' ? '#e8f5e9' : '#e3f2fd',
                    color: (manualType || detectedType) === 'metric' ? '#2e7d32' : '#1565c0',
                  }}>
                    {(manualType || detectedType) === 'metric' ? '📋 指标卡' : '📈 图表'}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--text-secondary)', flex: 1 }}>
                    {(manualType || detectedType) === 'metric'
                      ? '显示为单个数值卡片'
                      : '显示为 ECharts 图表'}
                  </span>
                  {/* 手动切换按钮 */}
                  <button onClick={() => setManualType(manualType === 'metric' ? 'chart' : 'metric')}
                    style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, border: '1px solid var(--border-color)', background: 'var(--bg-input)', cursor: 'pointer', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                    切换为{(manualType || detectedType) === 'metric' ? '图表' : '指标'}
                  </button>
                </div>
              )}
            </div>

            <div style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>选择目标大屏：</label>
              <select value={selectedDashboard} onChange={e => setSelectedDashboard(e.target.value)}
                style={{ width: '100%', padding: '8px 10px', fontSize: 13, background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 8, color: 'var(--text-primary)', outline: 'none' }}>
                {dashboardList.length === 0 && <option value="">（暂无大屏，请先在大屏面板创建）</option>}
                {dashboardList.map(name => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button onClick={() => { addToDashboard(confirmModal.msg, selectedDashboard, manualType || detectedType); setConfirmModal(null) }}
                disabled={dashboardList.length === 0}
                style={{ padding: '8px 28px', fontSize: 13, cursor: dashboardList.length === 0 ? 'not-allowed' : 'pointer', background: dashboardList.length === 0 ? 'var(--bg-hover)' : 'var(--accent)', color: dashboardList.length === 0 ? 'var(--text-muted)' : '#fff', border: 'none', borderRadius: 8, fontWeight: 500 }}>确定添加</button>
              <button onClick={() => setConfirmModal(null)}
                style={{ padding: '8px 28px', fontSize: 13, cursor: 'pointer', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 8, color: 'var(--text-muted)' }}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const selStyle = { padding: '3px 6px', fontSize: 11, borderRadius: 4, background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)', outline: 'none' }
