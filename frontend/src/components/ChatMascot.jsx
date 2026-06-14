import { useState, useEffect, useRef, useCallback } from 'react'

/* ==========================================================================
   ChatMascot — 像素小猫（设计稿实现 30×32px）
   纯 CSS box-shadow 绘制，无外部依赖
   配色：黑轮廓 #000 / 浅灰 #BDBDBD / 深灰 #828282 / 蓝眼 #2196F3 / 白高光
   ========================================================================== */

// ---- 设计稿：15×16 网格 × 2px = 30×32px ----
//  b = 黑 #000        l = 浅灰 #BDBDBD
//  d = 深灰 #828282    u = 蓝眼 #2196F3
//  w = 白高光 #fff     y = 黄星星 #FFD700
//  p = 粉爱心 #FF69B4  ' ' = 透明

const W = 15, H = 16, PS = 2

const BASE = [
  '    bbbbb      ',  //  0 top
  'bddlllllldlb   ',  //  1 ears
  'bddlllllldb    ',  //  2 ears
  ' bllllllllb    ',  //  3
  ' bllllllllb    ',  //  4
  ' blbwubbwub    ',  //  5 eyes
  ' blbuubbuub    ',  //  6 eyes
  ' bllllllllb    ',  //  7
  ' bllllllllb    ',  //  8
  '  bllllllb     ',  //  9
  '  bllllllb     ',  // 10
  '   blllblb     ',  // 11
  '   bllbbl lb b ',  // 12
  '    bbblll bbdb',  // 13 tail
  '    bbbbbbbbbdd',  // 14 base
  '           b b ',  // 15 feet
]

// ---- 表情变体（仅修改眼部） ----
// 闭眼：眼位变浅灰
const BLINK = BASE.map((r, i) => {
  if (i === 5) return ' blbllllllb    '
  if (i === 6) return ' blbllllllb    '
  return r
})

// 思考眼：= =
const THINK = BASE.map((r, i) => {
  if (i === 5) return ' blb==ll==b    '
  if (i === 6) return ' blb==ll==b    '
  return r
})

// 星星眼
const HAPPY = BASE.map((r, i) => {
  if (i === 5) return ' blbyyulyub    '
  if (i === 6) return ' blbyyullb    '
  return r
})

// 爱心眼
const LOVE = BASE.map((r, i) => {
  if (i === 5) return ' blbppllppb    '
  if (i === 6) return ' blbppllppb    '
  return r
})

/* ---- 像素 → box-shadow ---- */
function gridToShadow(rows, size) {
  const s = []
  for (let y = 0; y < rows.length; y++) {
    for (let x = 0; x < rows[y].length; x++) {
      const ch = rows[y][x]; if (ch === ' ') continue
      const px = x * size, py = y * size
      switch (ch) {
        case 'b': s.push(`${px}px ${py}px 0 #000`); break
        case 'l': s.push(`${px}px ${py}px 0 #BDBDBD`); break
        case 'd': s.push(`${px}px ${py}px 0 #828282`); break
        case 'u': s.push(`${px}px ${py}px 0 #2196F3`); break
        case 'w': s.push(`${px}px ${py}px 0 #fff`); break
        case 'y': s.push(`${px}px ${py}px 0 #FFD700`); break
        case 'p': s.push(`${px}px ${py}px 0 #FF69B4`); break
        case '=': s.push(`${px}px ${py}px 0 #555`); break
      }
    }
  }
  return s.join(', ')
}

const S = {
  idle: gridToShadow(BASE, PS),
  blink: gridToShadow(BLINK, PS),
  think: gridToShadow(THINK, PS),
  happy: gridToShadow(HAPPY, PS),
  love: gridToShadow(LOVE, PS),
}

/* ---- CSS 动画 ---- */
const KF = `
@keyframes mB { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-1px)} }
@keyframes mT { 0%,100%{transform:rotate(0)} 25%{transform:rotate(5deg)} 75%{transform:rotate(-3deg)} }
@keyframes mH {
  0%{transform:scale(1) rotate(0)}
  20%{transform:scale(1.2) rotate(-6deg)}
  40%{transform:scale(1.25) rotate(6deg)}
  60%{transform:scale(1.1) rotate(-3deg)}
  100%{transform:scale(1) rotate(0)}
}
@keyframes mG {
  0%,100%{filter:drop-shadow(0 0 2px #8A9BAE)}
  50%{filter:drop-shadow(0 0 5px #8A9BAE) drop-shadow(0 0 10px rgba(138,155,174,0.3))}
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
@keyframes mSk { 0%,100%{transform:translateX(0)} 25%{transform:translateX(2px)} 75%{transform:translateX(-2px)} }
@keyframes mJ { 0%,100%{transform:translateY(0)} 40%{transform:translateY(-6px)} 60%{transform:translateY(-4px)} }
@media (max-width:640px){ .mr{transform:scale(0.7);transform-origin:top right} }
`

const Wpx = W * PS, Hpx = H * PS

export default function ChatMascot({ status = 'idle', style }) {
  const [face, setFace] = useState('idle')
  const [extraAnim, setExtraAnim] = useState('')
  const [clicked, setClicked] = useState(false)
  const prevSt = useRef(status)
  const [justLanded, setJustLanded] = useState(false)

  // 状态切换 → 落地弹跳
  useEffect(() => {
    if (prevSt.current === 'thinking' && status === 'idle') {
      setJustLanded(true)
      const t = setTimeout(() => setJustLanded(false), 600)
      prevSt.current = status; return () => clearTimeout(t)
    }
    prevSt.current = status
  }, [status])

  // idle 微动画：眨眼 / 摇头 / 耳抖 / 尾摆
  useEffect(() => {
    if (status !== 'idle' || clicked) return
    const timers = []
    const blink = () => {
      const t = setTimeout(() => {
        setFace('blink')
        setTimeout(() => setFace('idle'), 160)
        blink()
      }, 2000 + Math.random() * 2500)
      timers.push(t)
    }
    blink()
    const shake = () => {
      const t = setTimeout(() => {
        setExtraAnim('mT')
        setTimeout(() => setExtraAnim(''), 600)
        shake()
      }, 4000 + Math.random() * 3000)
      timers.push(t)
    }
    shake()
    const tail = () => {
      const t = setTimeout(() => {
        setExtraAnim('mSk')
        setTimeout(() => setExtraAnim(''), 800)
        tail()
      }, 5000 + Math.random() * 4000)
      timers.push(t)
    }
    tail()
    return () => timers.forEach(clearTimeout)
  }, [status, clicked])

  // thinking 子动画：思考眼 + 小跑 + 随机跳 + 转身
  useEffect(() => {
    if (status !== 'thinking' || clicked) return
    const timers = []
    setFace('think')
    const hop = () => {
      const t = setTimeout(() => {
        setExtraAnim('mJ')
        setTimeout(() => setExtraAnim(''), 400)
        hop()
      }, 5000 + Math.random() * 3000)
      timers.push(t)
    }
    hop()
    return () => { timers.forEach(clearTimeout); setFace('idle') }
  }, [status, clicked])

  // 点击互动
  const handleClick = useCallback(() => {
    if (clicked) return
    setClicked(true)
    setFace('love')
    setExtraAnim('mH')
    setTimeout(() => {
      setClicked(false)
      setFace(status === 'thinking' ? 'think' : 'idle')
      setExtraAnim('')
    }, 1500)
  }, [clicked, status])

  // 合成动画
  const buildAnim = () => {
    const parts = []
    if (clicked) {
      parts.push('mH 1.5s ease-in-out')
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
    if (extraAnim) parts.push(extraAnim + ' 0.6s ease-in-out')
    return parts.join(', ')
  }

  const shadow = clicked ? S.love
    : face === 'blink' ? S.blink
    : face === 'think' ? S.think
    : S.idle

  return (
    <>
      <style>{KF}</style>
      <div
        className="mr"
        onClick={handleClick}
        title="🐱 点击小猫"
        style={{
          position: 'absolute', width: Wpx, height: Hpx,
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
