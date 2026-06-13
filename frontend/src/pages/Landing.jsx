import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'

const LINE_COUNT = 6
const TITLE = 'Text to SQL'

export default function Landing({ theme, toggleTheme }) {
  const navigate = useNavigate()
  const [exiting, setExiting] = useState(false)
  const [revealed, setRevealed] = useState(0) // 已打字的字符数
  const [titleDone, setTitleDone] = useState(false)

  // 打字机效果：每 80ms  reveal 一个字符
  useEffect(() => {
    if (revealed < TITLE.length) {
      const timer = setTimeout(() => setRevealed(r => r + 1), 80)
      return () => clearTimeout(timer)
    } else {
      // 全部 reveal 后延迟一点点标记完成，让其他元素入场
      const timer = setTimeout(() => setTitleDone(true), 200)
      return () => clearTimeout(timer)
    }
  }, [revealed])

  const handleEnter = () => {
    setExiting(true)
    setTimeout(() => navigate('/chat'), 700)
  }

  return (
    <motion.div
      style={{
        height: '100vh', width: '100%',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg-primary)', position: 'relative', overflow: 'hidden',
      }}
      animate={exiting ? { opacity: 0, y: -40 } : { opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: 'easeInOut' }}
    >
      {/* Background image layer */}
      <div style={{
        position: 'absolute', inset: 0, zIndex: 0,
        backgroundImage: 'url(/background.png)',
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        opacity: 0.25,
      }} />

      {/* Overlay to ensure text readability */}
      <div style={{
        position: 'absolute', inset: 0, zIndex: 1,
        background: 'linear-gradient(135deg, var(--bg-primary) 0%, transparent 50%, var(--bg-primary) 100%)',
      }} />

      {/* Flowing background lines */}
      <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0.15, zIndex: 2 }}>
        <defs>
          <linearGradient id="lg" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.3" />
            <stop offset="50%" stopColor="var(--accent)" stopOpacity="0.05" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.3" />
          </linearGradient>
        </defs>
        {Array.from({ length: LINE_COUNT }).map((_, i) => (
          <motion.path
            key={i}
            d={`M${-50 + i * 40},${200 + i * 80} Q${300 + i * 100},${100 + i * 40} ${600 + i * 60},${300 + i * 60} T${1100},${200 + i * 50}`}
            stroke="url(#lg)"
            strokeWidth="1.5"
            fill="none"
            animate={{ pathLength: [0, 1, 0], opacity: [0.2, 0.6, 0.2] }}
            transition={{ duration: 6 + i * 1.5, repeat: Infinity, ease: 'easeInOut', delay: i * 0.8 }}
          />
        ))}
      </svg>

      {/* Theme toggle */}
      <button onClick={toggleTheme} style={{
        position: 'absolute', top: 20, right: 20, zIndex: 10,
        background: 'none', border: '1px solid var(--border-color)',
        borderRadius: 8, padding: '6px 12px',
        color: 'var(--text-muted)', cursor: 'pointer', fontSize: 13,
      }}>
        {theme === 'light' ? '🌙' : '☀️'}
      </button>

      <div style={{ textAlign: 'center', maxWidth: 480, padding: 20, zIndex: 10 }}>
        {/* Title – 打字机效果 */}
        <h1
          style={{
            fontSize: 'clamp(3.2rem, 8vw, 5.5rem)',
            fontFamily: "'Playfair Display', 'Georgia', serif",
            fontWeight: 400, letterSpacing: '-0.03em',
            color: 'var(--accent)', margin: '0 0 4px', minHeight: '1.3em',
          }}
        >
          {TITLE.split('').map((char, i) => {
            // "SQL" 部分（索引 8-10）使用灰蓝色意式斜体
            const isSQL = i >= 8 && i <= 10
            return (
              <motion.span
                key={i}
                initial={{ opacity: 0 }}
                animate={{ opacity: i < revealed ? 1 : 0 }}
                transition={{ duration: 0.05 }}
                style={isSQL ? { color: '#a2c7db', fontStyle: 'italic', fontFamily: "'Georgia', serif", fontSize: '1.15em' } : {}}
              >
                {char === ' ' ? ' ' : char}
              </motion.span>
            )
          })}
          {/* 光标：打字期间闪烁，打完消失 */}
          <motion.span
            animate={{ opacity: titleDone ? 0 : [1, 0, 1, 0, 1, 0, 1] }}
            transition={{ duration: 0.6, repeat: titleDone ? 0 : Infinity }}
            style={{ marginLeft: 2, fontWeight: 300 }}
          >|</motion.span>
        </h1>

        {/* Subtitle – 标题完成后出现 */}
        <AnimatePresence>
          {titleDone && (
            <motion.p
              key="subtitle"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, ease: 'easeOut' }}
              style={{ color: 'var(--text-secondary)', fontSize: '1.15rem', margin: '0 0 36px' }}
            >
              用自然语言，探索你的数据宇宙
            </motion.p>
          )}
        </AnimatePresence>

        {/* Tags – 标题完成后出现 */}
        <AnimatePresence>
          {titleDone && (
            <motion.div
              key="tags"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.15, ease: 'easeOut' }}
              style={{ display: 'flex', gap: 12, justifyContent: 'center', marginBottom: 40, flexWrap: 'wrap' }}
            >
              {['私有化部署', '智能分析', '自动图表'].map(tag => (
                <span key={tag} style={{
                  padding: '5px 14px', borderRadius: 20,
                  background: 'var(--bg-input)', color: 'var(--text-muted)',
                  fontSize: 13, border: '1px solid var(--border-color)',
                }}>{tag}</span>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        {/* CTA – 标题完成后出现 */}
        <AnimatePresence>
          {titleDone && (
            <motion.button
              key="cta"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.3, ease: 'easeOut' }}
              onClick={handleEnter}
              whileHover={{ scale: 1.03, boxShadow: '0 4px 24px rgba(138,155,174,0.3)' }}
              whileTap={{ scale: 0.97 }}
              style={{
                padding: '12px 40px', borderRadius: 24,
                border: 'none', background: 'var(--accent)',
                color: '#fff', fontSize: 17, cursor: 'pointer',
                transition: 'all 0.2s',
              }}
            >
              开始探索
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      {/* Blink keyframes injected once */}
      <style>{'@keyframes blink { 50% { opacity: 0 } }'}</style>
    </motion.div>
  )
}
