import { useState, useEffect, useRef, useCallback } from 'react'

/* ==========================================================================
   ChatMascot — 像素灰蓝色小猫公仔组件
   纯 CSS 像素风，无外部依赖，通过 box-shadow 绘制

   设计：
     - idle 时固定在输入框右上（由父组件传 position）
     - thinking 时跳至 AI 气泡上方来回跑跳
     - 点击播放开心动画（星星眼 + 弹跳 + 光晕）
   ========================================================================== */

// ---- 像素猫定义（16×14 网格，每个字符代表一个像素） ----
//   c  = 猫身体主色（灰蓝）
//   e  = 眼白
//   p  = 瞳孔
//   n  = 鼻子（淡粉）
//   ' ' = 透明
const CAT_ROWS = [
  '                ',
  '      cccc      ',
  '    cccccccc    ',
  '   cc  cc  cc   ',
  '  ccc  cc  ccc  ',
  '  cccccccccc    ',
  '  ccc cc  cc    ',
  '  cc e    e cc  ',
  '  cc  p  p  cc  ',
  '  cc   nn   cc  ',
  '  ccc  cc  ccc  ',
  '   ccc    ccc   ',
  '    cccccccc    ',
  '     cc  cc     ',
]

const PIXEL_SIZE = 5
const CAT_COLOR = 'var(--accent)'
const CAT_WIDTH = CAT_ROWS[0].length * PIXEL_SIZE // 80px
const CAT_HEIGHT = CAT_ROWS.length * PIXEL_SIZE    // 70px

/** 将像素网格转为 CSS box-shadow 值 */
function gridToBoxShadow(rows, size, color) {
  const shadows = []
  for (let y = 0; y < rows.length; y++) {
    for (let x = 0; x < rows[y].length; x++) {
      const ch = rows[y][x]
      const px = x * size
      const py = y * size
      switch (ch) {
        case 'c':
          shadows.push(`${px}px ${py}px 0 ${color}`)
          break
        case 'e':
          shadows.push(`${px}px ${py}px 0 #fff`)
          break
        case 'p':
          shadows.push(`${px}px ${py}px 0 #2a3a4a`)
          break
        case 'n':
          shadows.push(`${px}px ${py}px 0 #ffb3b3`)
          break
      }
    }
  }
  return shadows.join(', ')
}

/* 预制不同动画姿势的 box-shadow 值 */
const IDLE_SHADOW = gridToBoxShadow(CAT_ROWS, PIXEL_SIZE, CAT_COLOR)

// 开心姿势（眼睛变星星）
const HAPPY_ROWS = CAT_ROWS.map((row, i) => {
  if (i === 7) return '  cc *  *  cc  '
  if (i === 8) return '  cc  *  *  cc  '
  return row
})
const HAPPY_SHADOW = gridToBoxShadow(HAPPY_ROWS, PIXEL_SIZE, CAT_COLOR)

// 眨眼姿势
const BLINK_ROWS = CAT_ROWS.map((row, i) => {
  if (i === 7) return '  cc        cc  '
  if (i === 8) return '  cc        cc  '
  return row
})
const BLINK_SHADOW = gridToBoxShadow(BLINK_ROWS, PIXEL_SIZE, CAT_COLOR)

// ---- 动画 keyframes ----
const animationStyles = `
@keyframes mc-breathe {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-2px); }
}
@keyframes mc-tilt {
  0%, 100% { transform: rotate(0deg); }
  25% { transform: rotate(6deg); }
  75% { transform: rotate(-4deg); }
}
@keyframes mc-happy {
  0% { transform: scale(1) rotate(0deg); }
  20% { transform: scale(1.2) rotate(-6deg); }
  40% { transform: scale(1.25) rotate(6deg); }
  60% { transform: scale(1.1) rotate(-3deg); }
  80% { transform: scale(1.05) rotate(2deg); }
  100% { transform: scale(1) rotate(0deg); }
}
@keyframes mc-glow {
  0%, 100% { filter: drop-shadow(0 0 3px var(--accent)); }
  50% { filter: drop-shadow(0 0 10px var(--accent)) drop-shadow(0 0 20px rgba(138,155,174,0.3)); }
}
@keyframes mc-run {
  0% { transform: translateX(0) translateY(0); }
  15% { transform: translateX(20px) translateY(-5px); }
  30% { transform: translateX(40px) translateY(0); }
  45% { transform: translateX(55px) translateY(-4px); }
  60% { transform: translateX(40px) translateY(0); }
  75% { transform: translateX(20px) translateY(-3px); }
  100% { transform: translateX(0) translateY(0); }
}
@keyframes mc-bounce {
  0%, 100% { transform: scaleY(1); }
  30% { transform: scaleY(0.9); }
  60% { transform: scaleY(1.03); }
}
@keyframes mc-land {
  0% { transform: translateY(-20px) scaleY(1.1); }
  40% { transform: translateY(0) scaleY(0.92); }
  65% { transform: translateY(-8px) scaleY(1.05); }
  85% { transform: translateY(0) scaleY(0.97); }
  100% { transform: translateY(0) scaleY(1); }
}
@keyframes mc-fade-pulse {
  0%, 100% { opacity: 0.8; }
  50% { opacity: 1; }
}
/* 移动端缩小 */
@media (max-width: 640px) {
  .mc-resp { transform: scale(0.7); transform-origin: top right; }
}
`

/**
 * ChatMascot — 像素小猫公仔
 *
 * Props:
 *   status: 'idle' | 'thinking'      — 控制小猫状态
 *   style?: object                    — 外层容器附加样式（top/left/right/bottom 定位）
 *
 * 行为：
 *   idle    — 固定在父组件指定位置，带呼吸/眨眼微动画
 *   thinking— 在气泡上方小跑往返，模拟忙碌工作
 *   点击    — 播放开心动画（星星眼 + 弹跳 + 光晕），1.5s 后恢复
 */
export default function ChatMascot({ status = 'idle', style }) {
  const [clickEffect, setClickEffect] = useState(false)
  const [blink, setBlink] = useState(false)
  const [justLanded, setJustLanded] = useState(false)
  const prevStatus = useRef(status)

  // ---- 状态切换时触发落地弹跳 ----
  useEffect(() => {
    if (prevStatus.current === 'thinking' && status === 'idle') {
      setJustLanded(true)
      const timer = setTimeout(() => setJustLanded(false), 600)
      prevStatus.current = status
      return () => clearTimeout(timer)
    }
    prevStatus.current = status
  }, [status])

  // ---- 随机眨眼 ----
  useEffect(() => {
    if (status !== 'idle' || clickEffect) return
    const scheduleBlink = () => {
      const delay = 2000 + Math.random() * 4000
      return setTimeout(() => {
        setBlink(true)
        setTimeout(() => setBlink(false), 180)
        scheduleBlink()
      }, delay)
    }
    const timer = scheduleBlink()
    return () => clearTimeout(timer)
  }, [status, clickEffect])

  // ---- 点击开心动画 ----
  const handleClick = useCallback(() => {
    if (clickEffect) return
    setClickEffect(true)
    setBlink(false)
    setTimeout(() => setClickEffect(false), 1500)
  }, [clickEffect])

  // ---- 计算动画 ----
  let animationName = ''
  let shadowValue = IDLE_SHADOW

  if (clickEffect) {
    animationName = 'mc-happy 1.5s ease-in-out, mc-glow 0.8s ease-in-out 2'
    shadowValue = HAPPY_SHADOW
  } else if (status === 'thinking') {
    animationName = 'mc-glow 1.5s ease-in-out infinite, mc-run 1.2s ease-in-out infinite, mc-bounce 0.6s ease-in-out infinite'
    shadowValue = IDLE_SHADOW
  } else if (justLanded) {
    animationName = 'mc-land 0.6s cubic-bezier(0.34, 1.56, 0.64, 1), mc-glow 2s ease-in-out infinite'
    shadowValue = IDLE_SHADOW
  } else {
    const tilt = blink ? '' : ', mc-tilt 3s ease-in-out'
    animationName = 'mc-breathe 2.5s ease-in-out infinite' + tilt + ', mc-fade-pulse 4s ease-in-out infinite, mc-glow 2s ease-in-out infinite'
    shadowValue = blink ? BLINK_SHADOW : IDLE_SHADOW
  }

  return (
    <>
      <style>{animationStyles}</style>
      <div
        className="mc-resp"
        onClick={handleClick}
        title="点击小猫 🐱"
        style={{
          position: 'absolute',
          width: CAT_WIDTH,
          height: CAT_HEIGHT,
          zIndex: 100,
          cursor: 'pointer',
          animation: animationName,
          transition: 'left 0.5s cubic-bezier(0.34, 1.56, 0.64, 1), top 0.5s cubic-bezier(0.34, 1.56, 0.64, 1), right 0.5s cubic-bezier(0.34, 1.56, 0.64, 1), bottom 0.5s cubic-bezier(0.34, 1.56, 0.64, 1)',
          pointerEvents: 'auto',
          background: 'transparent',
          ...(style || {}),
          // 像素猫用 box-shadow 绘制；mc-glow 改用 filter drop-shadow 以避免冲突
          boxShadow: shadowValue,
        }}
      />
    </>
  )
}
