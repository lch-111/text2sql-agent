import { useState, useEffect, useRef, useCallback } from 'react'

/* ==========================================================================
   ChatMascot — 桌面小宠物风格像素小猫（超级迷你 32×28px）
   纯 CSS box-shadow 像素风，无外部依赖
   ========================================================================== */

// ---- 像素猫定义（16×14 网格 × 2px = 32×28px）----
//  c = 主色灰蓝, C = 深灰蓝(阴影), e = 眼白, p = 瞳孔
//  n = 鼻子淡粉, h = 高光, k = 粉色耳内, t = 尾巴
//  s = 腮红, a = 爱心, w = 星星眼, ' ' = 透明

const COLS = 16, ROWS = 14

// 默认站姿（圆脸 + 大眼 + 尾巴）
const IDLE = [
  '                ',
  '      cccc      ',
  '    cccccccc    ',
  '   cc  kk  cc   ',
  '  cc  cc  cc    ',
  '  cccccccccc    ',
  '  ccee  eec     ',
  '  cc pp  pp c   ',
  '  cc  nn   c    ',
  '  ccc nn ccc    ',
  '   cc cc cc t   ',
  '    c    c  t   ',
  '   cc      cc   ',
  '  cc        cc  ',
]

// 闭眼（眨眼第一帧）
const BLINK1 = IDLE.map(r => r.replace(/ee/g, '  ').replace(/pp/g, '  '))

// 半闭眼（快速双眨第二帧）
const BLINK2 = IDLE.map(r => {
  if (r.includes('ccee')) return '  ccs  ssc     '
  if (r.includes('cc pp')) return '  cc    cc     '
  return r
})

// 星星眼（开心）
const HAPPY = IDLE.map(r => {
  if (r.includes('ccee')) return '  ccww  wwc     '
  if (r.includes('cc pp')) return '  cc ww ww c    '
  return r
})

// 爱心眼 + 腮红爱心（点击互动）
const LOVE = IDLE.map(r => {
  if (r.includes('ccee')) return '  cc<>  <>c     '
  if (r.includes('cc pp')) return '  cc << >> c    '
  if (r.includes('  ccee')) return '  ccaa  aac     '
  return r
})

// 思考眼 = =
const THINK = IDLE.map(r => {
  if (r.includes('ccee')) return '  cc==  ==c     '
  if (r.includes('cc pp')) return '  cc == == c    '
  return r
})

// 单耳抖动（左耳）
const EARTWITCH = IDLE.map((r, i) => {
  if (i === 1) return '   cccc       '
  if (i === 2) return '  cccccccc    '
  if (i === 3) return ' c   kk  cc   '
  return r
})

// 尾巴翘起
const TAILUP = IDLE.map((r, i) => {
  if (i === 8) return '  cc  nn   c t '
  if (i === 9) return '  ccc nn ccc t '
  if (i === 10) return '   cc cc cc  t '
  if (i === 11) return '    c    c   t '
  return r
})

// 单脚跳
const HOP = IDLE.map((r, i) => {
  if (i === 13) return '  c    c       '
  return r
})

/* ---- 像素网格 → box-shadow ---- */
function gridToShadow(rows, size, color) {
  const s = []
  for (let y = 0; y < rows.length; y++) {
    for (let x = 0; x < rows[y].length; x++) {
      const ch = rows[y][x]; if (ch === ' ') continue
      const px = x * size, py = y * size
      switch (ch) {
        case 'c': s.push(`${px}px ${py}px 0 ${color}`); break
        case 'C': s.push(`${px}px ${py}px 0 #6B7D8E`); break
        case 'e': s.push(`${px}px ${py}px 0 #fff`); break
        case 'p': s.push(`${px}px ${py}px 0 #2a3a4a`); break
        case 'n': s.push(`${px}px ${py}px 0 #ffb3b3`); break
        case 'h': s.push(`${px}px ${py}px 0 #fff`); break
        case 'k': s.push(`${px}px ${py}px 0 #f0c0c0`); break
        case 't': s.push(`${px}px ${py}px 0 var(--accent)`); break
        case 's': s.push(`${px}px ${py}px 0 #ffb3b3`); break
        case 'a': s.push(`${px}px ${py}px 0 #ff6b8a`); break
        case 'w': s.push(`${px}px ${py}px 0 #ffd700`); break
        case '<': s.push(`${px}px ${py}px 0 #ff4081`); break
        case '>': s.push(`${px}px ${py}px 0 #ff4081`); break
        case '=': s.push(`${px}px ${py}px 0 #555`); break
      }
    }
  }
  return s.join(', ')
}

const PS = 2, CC = 'var(--accent)'
const W = COLS * PS, H = ROWS * PS

/* ---- 所有像素帧 ---- */
const SHADOWS = {
  idle: gridToShadow(IDLE, PS, CC),
  blink: gridToShadow(BLINK1, PS, CC),
  blink2: gridToShadow(BLINK2, PS, CC),
  happy: gridToShadow(HAPPY, PS, CC),
  love: gridToShadow(LOVE, PS, CC),
  think: gridToShadow(THINK, PS, CC),
  ear: gridToShadow(EARTWITCH, PS, CC),
  tail: gridToShadow(TAILUP, PS, CC),
  hop: gridToShadow(HOP, PS, CC),
}

/* ---- CSS 动画关键帧 ---- */
const KF = `
@keyframes mB { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-1px)} }
@keyframes mT { 0%,100%{transform:rotate(0)} 25%{transform:rotate(5deg)} 75%{transform:rotate(-3deg)} }
@keyframes mH {
  0%{transform:scale(1) rotate(0)}
  20%{transform:scale(1.15) rotate(-5deg)}
  40%{transform:scale(1.2) rotate(5deg)}
  60%{transform:scale(1.1) rotate(-3deg)}
  100%{transform:scale(1) rotate(0)}
}
@keyframes mL {
  0%{transform:scale(1)}
  20%{transform:scale(1.2) rotate(-8deg)}
  40%{transform:scale(1.25) rotate(8deg)}
  60%{transform:scale(1.1) rotate(-4deg)}
  80%{transform:scale(1.05) rotate(3deg)}
  100%{transform:scale(1) rotate(0)}
}
@keyframes mG {
  0%,100%{filter:drop-shadow(0 0 2px var(--accent))}
  50%{filter:drop-shadow(0 0 5px var(--accent)) drop-shadow(0 0 10px rgba(138,155,174,0.3))}
}
@keyframes mR {
  0%{transform:translateX(0) translateY(0)}
  15%{transform:translateX(8px) translateY(-2px)}
  30%{transform:translateX(16px) translateY(0)}
  45%{transform:translateX(22px) translateY(-2px)}
  60%{transform:translateX(16px) translateY(0)}
  75%{transform:translateX(8px) translateY(-1px)}
  100%{transform:translateX(0) translateY(0)}
}
@keyframes mBc { 0%,100%{transform:scaleY(1)} 30%{transform:scaleY(0.92)} 60%{transform:scaleY(1.03)} }
@keyframes mLa {
  0%{transform:translateY(-8px) scaleY(1.1)}
  40%{transform:translateY(0) scaleY(0.94)}
  65%{transform:translateY(-4px) scaleY(1.05)}
  85%{transform:translateY(0) scaleY(0.97)}
  100%{transform:translateY(0) scaleY(1)}
}
@keyframes mSk {
  0%,100%{transform:translateX(0)}
  25%{transform:translateX(2px)}
  75%{transform:translateX(-2px)}
}
@keyframes mJ {
  0%,100%{transform:translateY(0)}
  40%{transform:translateY(-6px)}
  60%{transform:translateY(-4px)}
}
@keyframes mSS { 0%,100%{transform:rotate(0)} 50%{transform:rotate(360deg)} }
@media (max-width:640px){ .mr{transform:scale(0.7);transform-origin:top right} }
`

/**
 * ChatMascot — 桌面小宠物风格像素小猫
 * Props: status ('idle'|'thinking'), style (定位)
 */
export default function ChatMascot({ status = 'idle', style }) {
  const [face, setFace] = useState('idle')  // 当前表情帧
  const [extraAnim, setExtraAnim] = useState('')
  const [clicked, setClicked] = useState(false)
  const prevSt = useRef(status)
  const [justLanded, setJustLanded] = useState(false)

  // ---- 状态切换：落地弹跳 ----
  useEffect(() => {
    if (prevSt.current === 'thinking' && status === 'idle') {
      setJustLanded(true)
      const t = setTimeout(() => setJustLanded(false), 600)
      prevSt.current = status; return () => clearTimeout(t)
    }
    prevSt.current = status
  }, [status])

  // ---- idle 微动画调度 ----
  useEffect(() => {
    if (status !== 'idle' || clicked) return
    const timers = []

    // 眨眼（15% 双眨）
    const scheduleBlink = () => {
      const t = setTimeout(() => {
        if (Math.random() < 0.15) {
          // 双眨
          setFace('blink')
          setTimeout(() => { setFace('blink2') }, 120)
          setTimeout(() => { setFace('idle') }, 240)
        } else {
          setFace('blink')
          setTimeout(() => setFace('idle'), 160)
        }
        scheduleBlink()
      }, 2000 + Math.random() * 2500)
      timers.push(t)
    }
    scheduleBlink()

    // 摇头
    const scheduleShake = () => {
      const t = setTimeout(() => {
        setExtraAnim('mT')
        setTimeout(() => setExtraAnim(''), 600)
        scheduleShake()
      }, 4000 + Math.random() * 3000)
      timers.push(t)
    }
    scheduleShake()

    // 耳朵抖动
    const scheduleEar = () => {
      const t = setTimeout(() => {
        setFace('ear')
        setTimeout(() => setFace('idle'), 200)
        scheduleEar()
      }, 5000 + Math.random() * 4000)
      timers.push(t)
    }
    scheduleEar()

    // 尾巴摆动（用摇尾巴动画 CSS）
    const scheduleTail = () => {
      const t = setTimeout(() => {
        setExtraAnim(prev => prev === 'mSk' ? '' : 'mSk')
        setTimeout(() => setExtraAnim(''), 800)
        scheduleTail()
      }, 6000 + Math.random() * 3000)
      timers.push(t)
    }
    scheduleTail()

    return () => timers.forEach(clearTimeout)
  }, [status, clicked])

  // ---- thinking 子动画调度 ----
  useEffect(() => {
    if (status !== 'thinking' || clicked) return
    const timers = []

    // 思考眼
    setFace('think')

    // 随机单脚跳
    const scheduleHop = () => {
      const t = setTimeout(() => {
        setFace('hop')
        setTimeout(() => setFace('think'), 300)
        scheduleHop()
      }, 5000 + Math.random() * 3000)
      timers.push(t)
    }
    scheduleHop()

    // 转身动画（用 scaleX 闪现）
    const scheduleTurn = () => {
      const t = setTimeout(() => {
        setExtraAnim(prev => prev.includes('mR') ? prev : 'mR') // trigger re-run
        setTimeout(() => {
          setExtraAnim('')
        }, 200)
        scheduleTurn()
      }, 4000 + Math.random() * 2000)
      timers.push(t)
    }
    scheduleTurn()

    return () => {
      timers.forEach(clearTimeout)
      setFace('idle')
    }
  }, [status, clicked])

  // ---- 点击互动 ----
  const handleClick = useCallback(() => {
    if (clicked) return
    setClicked(true)
    setFace('love')
    setExtraAnim('mL')
    // 不中断思考：点击开心结束时恢复思考/空闲
    setTimeout(() => {
      setClicked(false)
      setFace(status === 'thinking' ? 'think' : 'idle')
      setExtraAnim('')
    }, 1500)
  }, [clicked, status])

  // ---- 合成动画名 ----
  const buildAnim = () => {
    const parts = []
    if (clicked) {
      parts.push('mL 1.5s ease-in-out')
      parts.push('mG 0.6s ease-in-out 2')
    } else if (status === 'thinking') {
      parts.push('mG 1.5s ease-in-out infinite')
      parts.push('mR 1.2s ease-in-out infinite')
      parts.push('mBc 0.6s ease-in-out infinite')
    } else if (justLanded) {
      parts.push('mLa 0.6s cubic-bezier(0.34,1.56,0.64,1)')
      parts.push('mG 2s ease-in-out infinite')
    } else {
      parts.push('mB 2.5s ease-in-out infinite')
      parts.push('mG 2s ease-in-out infinite')
    }
    if (extraAnim && extraAnim !== 'mR') parts.push(extraAnim + ' 0.6s ease-in-out')
    return parts.join(', ')
  }

  const shadow = clicked ? SHADOWS.love
    : face === 'blink' ? SHADOWS.blink
    : face === 'blink2' ? SHADOWS.blink2
    : face === 'think' ? SHADOWS.think
    : face === 'hop' ? SHADOWS.hop
    : face === 'ear' ? SHADOWS.ear
    : SHADOWS.idle

  return (
    <>
      <style>{KF}</style>
      <div
        className="mr"
        onClick={handleClick}
        title="🐱 点击小猫"
        style={{
          position: 'absolute', width: W, height: H,
          zIndex: 100, cursor: 'pointer', pointerEvents: 'auto',
          background: 'transparent',
          animation: buildAnim(),
          transition: 'left 0.5s cubic-bezier(0.34,1.56,0.64,1), top 0.5s cubic-bezier(0.34,1.56,0.64,1), right 0.5s cubic-bezier(0.34,1.56,0.64,1), bottom 0.5s cubic-bezier(0.34,1.56,0.64,1)',
          boxShadow: shadow,
          ...(style || {}),
        }}
      />
    </>
  )
}
