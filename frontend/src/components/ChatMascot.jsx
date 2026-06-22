import { useState, useCallback, useRef, useEffect } from 'react'

/* ==========================================================================
   ChatMascot — 点击循环探头小猫
   16×12 网格 @4px = 64×48px
   通过 translateY 位移 + overflow hidden 实现探头/躲藏效果
   ========================================================================== */

const W = 16, PS = 4
const H = 12

// 像素字符定义
const GRID = [
  '    bb    bb    ',
  '   bbdb  bdbb   ',
  '  bbbbbbbbbbbb  ',
  ' bbbbbbbbbbbbbb ',
  ' bbbwebbbbewbbb ',
  ' bppbbbbbbbbppb ',
  '  bbbbbbbbbbbb bb',
  '  bbbbbbbbbbbb b',
  '   bbbbbbbbbbbbb',
  '   bbbbbbbbbb   ',
  '     bb  bb     ',
  '                ',
]

function gridToPx(rows, size) {
  const shadows = []
  for (let y = 0; y < rows.length; y++) {
    for (let x = 0; x < rows[y].length; x++) {
      const c = rows[y][x]
      if (c === ' ') continue
      const px = x * size, py = y * size
      const colorMap = {
        b: '#b1c5d8', d: '#8a8787',
        e: '#bee0fc', w: '#ffffff', p: '#e8d7dc',
      }
      shadows.push(`${px}px ${py}px 0 ${colorMap[c]}`)
    }
  }
  return shadows.join(', ')
}

const IDLE_PX = gridToPx(GRID, PS)
const BLINK_GRID = GRID.map(r => r.replace(/[ew]/g, 'b'))
const BLINK_PX = gridToPx(BLINK_GRID, PS)
const LOVE_GRID = GRID.map(r => r.replace(/[ew]/g, 'p'))
const LOVE_PX = gridToPx(LOVE_GRID, PS)

const CLIP_HEIGHT = 20
const STEP_OFFSETS = [0, -8, -8, -16]

const KEYFRAMES = `
@keyframes mNod {
  0%, 100% { transform: rotate(0); }
  25% { transform: rotate(6deg); }
  50% { transform: rotate(-2deg); }
  75% { transform: rotate(3deg); }
}
@keyframes mGlow {
  0%, 100% { filter: drop-shadow(0 0 4px rgba(189,189,189,0.5)); }
  50% { filter: drop-shadow(0 0 8px rgba(189,189,189,0.8)); }
}
`

export default function ChatMascot({ style }) {
  const [step, setStep] = useState(0)
  const [loveFlash, setLoveFlash] = useState(false)
  const flashTimerRef = useRef(null)

  // 点击循环探头
  const handleClick = useCallback(() => {
    // 爱心眼闪烁
    setLoveFlash(true)
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current)
    flashTimerRef.current = setTimeout(() => setLoveFlash(false), 600)
    // 推进步骤
    setStep(s => (s + 1) % 4)
  }, [])

  useEffect(() => {
    return () => {
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current)
    }
  }, [])

  const facePx = loveFlash ? LOVE_PX : (step === 2 ? BLINK_PX : IDLE_PX)
  const offset = STEP_OFFSETS[step]
  const catAnim = step === 3
    ? 'mNod 0.6s ease-in-out, mGlow 2s ease-in-out infinite'
    : 'mGlow 2s ease-in-out infinite'

  // 探头时取消裁剪并提升层级，保证全身可见
  const isPeeking = step > 0

  return (
    <>
      <style>{KEYFRAMES}</style>
      <div
        style={{
          position: 'absolute',
          width: 64,
          height: isPeeking ? 48 : CLIP_HEIGHT,
          overflow: isPeeking ? 'visible' : 'hidden',
          zIndex: isPeeking ? 11 : 0,
          pointerEvents: 'none',
          ...(style || {}),
        }}
      >
        {/* 猫像素 — 通过 translateY 控制上下位移 */}
        <div
          style={{
            width: PS,
            height: PS,
            background: 'transparent',
            animation: catAnim,
            transform: `translateY(${offset}px)`,
            transition: 'transform 0.35s cubic-bezier(0.34, 1.56, 0.64, 1)',
            boxShadow: facePx,
            willChange: 'transform',
            pointerEvents: 'none',
          }}
        />
        {/* 透明点击层 — 覆盖整个可见猫区域，确保点击命中 */}
        <div
          onClick={handleClick}
          title="🐱"
          style={{
            position: 'absolute',
            inset: 0,
            cursor: 'pointer',
            pointerEvents: 'auto',
            background: 'transparent',
            zIndex: 2,
          }}
        />
      </div>
    </>
  )
}
