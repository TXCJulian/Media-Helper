import { useRef, useEffect, useCallback } from 'react'

interface WaveformBarProps {
  peaks: number[]
  duration: number
  inPoint: number
  outPoint: number
  currentTime: number
  onInPointChange: (time: number) => void
  onOutPointChange: (time: number) => void
  onSeek: (time: number) => void
  size: 'large' | 'small'
  color?: string
}

type DragTarget = 'in' | 'out' | null

const HANDLE_HIT_PX = 8
const HANDLE_TAB_WIDTH = 6
const HANDLE_TAB_HEIGHT = 14

interface Palette {
  active: string
  glow: string
}

const DEFAULT_PALETTE: Palette = { active: '#34d399', glow: 'rgba(52, 211, 153, 0.35)' }

const COLORS = new Map<string, Palette>([
  ['emerald', DEFAULT_PALETTE],
  ['blue', { active: '#3b82f6', glow: 'rgba(59, 130, 246, 0.35)' }],
  ['rose', { active: '#f472b6', glow: 'rgba(244, 114, 182, 0.35)' }],
  ['purple', { active: '#a855f7', glow: 'rgba(168, 85, 247, 0.35)' }],
])

const SIZE_CONFIG = {
  large: { height: 280, radius: 16, barGap: 2, minBarWidth: 2 },
  small: { height: 56, radius: 8, barGap: 1, minBarWidth: 1 },
}

export default function WaveformBar({
  peaks,
  duration,
  inPoint,
  outPoint,
  currentTime,
  onInPointChange,
  onOutPointChange,
  onSeek,
  size,
  color = 'emerald',
}: WaveformBarProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<DragTarget>(null)
  const isDraggingRef = useRef(false)

  const palette = COLORS.get(color) ?? DEFAULT_PALETTE
  const config = SIZE_CONFIG[size]

  // ── Time / pixel conversions ──────────────────────────────────
  const timeToX = useCallback(
    (time: number, width: number) => (duration > 0 ? (time / duration) * width : 0),
    [duration],
  )

  const xToTime = useCallback(
    (x: number, rect: DOMRect) => {
      if (duration <= 0) return 0
      const ratio = Math.max(0, Math.min(1, x / rect.width))
      return ratio * duration
    },
    [duration],
  )

  // ── Canvas draw ───────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const rect = canvas.getBoundingClientRect()
    const w = rect.width * dpr
    const h = rect.height * dpr

    canvas.width = w
    canvas.height = h
    ctx.scale(dpr, dpr)

    const cssW = rect.width
    const cssH = rect.height

    // Background
    ctx.fillStyle = 'rgba(255, 255, 255, 0.03)'
    ctx.beginPath()
    ctx.roundRect(0, 0, cssW, cssH, config.radius)
    ctx.fill()

    // Trim overlay (dimmed regions outside in/out)
    const inX = timeToX(inPoint, cssW)
    const outX = timeToX(outPoint, cssW)

    ctx.fillStyle = 'rgba(0, 0, 0, 0.35)'
    if (inX > 0) {
      ctx.fillRect(0, 0, inX, cssH)
    }
    if (outX < cssW) {
      ctx.fillRect(outX, 0, cssW - outX, cssH)
    }

    // Waveform bars
    if (peaks.length > 0) {
      const barWidth = Math.max(
        config.minBarWidth,
        (cssW - config.barGap * peaks.length) / peaks.length,
      )
      const step = barWidth + config.barGap
      const centerY = cssH / 2
      const maxBarH = (cssH / 2) * 0.85

      for (let i = 0; i < peaks.length; i++) {
        const x = i * step
        if (x > cssW) break

        const peakVal = Math.max(0, Math.min(1, peaks[i] ?? 0))
        const barH = Math.max(1, peakVal * maxBarH)
        const isKept = x >= inX && x + barWidth <= outX

        ctx.fillStyle = isKept ? palette.active : 'rgba(255, 255, 255, 0.15)'
        // Top half
        ctx.fillRect(x, centerY - barH, barWidth, barH)
        // Bottom half (mirrored)
        ctx.fillRect(x, centerY, barWidth, barH)
      }
    }

    // ── Handle rendering helper ─────────────────────────────────
    const drawHandle = (xPos: number) => {
      // Vertical line
      ctx.strokeStyle = palette.active
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.moveTo(xPos, 0)
      ctx.lineTo(xPos, cssH)
      ctx.stroke()

      // Grab tab at top
      const tabW = HANDLE_TAB_WIDTH
      const tabH = HANDLE_TAB_HEIGHT
      ctx.fillStyle = palette.active
      ctx.beginPath()
      ctx.roundRect(xPos - tabW / 2, 0, tabW, tabH, [0, 0, 3, 3])
      ctx.fill()
    }

    drawHandle(inX)
    drawHandle(outX)

    // ── Playhead ────────────────────────────────────────────────
    const playX = timeToX(currentTime, cssW)
    ctx.strokeStyle = '#ffffff'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    ctx.moveTo(playX, 0)
    ctx.lineTo(playX, cssH)
    ctx.stroke()
  }, [peaks, duration, inPoint, outPoint, currentTime, size, color, palette, config, timeToX])

  // ── Hit-test which handle (if any) is near an x position ──────
  const hitTest = useCallback(
    (clientX: number): DragTarget => {
      const canvas = canvasRef.current
      if (!canvas) return null
      const rect = canvas.getBoundingClientRect()
      const x = clientX - rect.left
      const cssW = rect.width

      const inX = timeToX(inPoint, cssW)
      const outX = timeToX(outPoint, cssW)

      if (Math.abs(x - inX) <= HANDLE_HIT_PX) return 'in'
      if (Math.abs(x - outX) <= HANDLE_HIT_PX) return 'out'
      return null
    },
    [inPoint, outPoint, timeToX],
  )

  // ── Mouse handlers ────────────────────────────────────────────
  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const target = hitTest(e.clientX)
      if (target) {
        dragRef.current = target
        isDraggingRef.current = true
        e.preventDefault()
      } else {
        // Click to seek
        const canvas = canvasRef.current
        if (!canvas) return
        const rect = canvas.getBoundingClientRect()
        const time = xToTime(e.clientX - rect.left, rect)
        onSeek(time)
      }
    },
    [hitTest, xToTime, onSeek],
  )

  // Window-level move/up for drag outside canvas bounds
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDraggingRef.current || !dragRef.current) return
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const time = xToTime(e.clientX - rect.left, rect)

      if (dragRef.current === 'in') {
        const clamped = Math.max(0, Math.min(time, outPoint - 0.01))
        onInPointChange(clamped)
      } else {
        const clamped = Math.max(inPoint + 0.01, Math.min(time, duration))
        onOutPointChange(clamped)
      }
    }

    const handleMouseUp = () => {
      dragRef.current = null
      isDraggingRef.current = false
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [inPoint, outPoint, duration, xToTime, onInPointChange, onOutPointChange])

  // ── Cursor management ─────────────────────────────────────────
  const handleCanvasMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current
      if (!canvas) return

      if (isDraggingRef.current) {
        canvas.style.cursor = 'grabbing'
        return
      }

      const target = hitTest(e.clientX)
      canvas.style.cursor = target ? 'grab' : 'pointer'
    },
    [hitTest],
  )

  const containerClass =
    size === 'large'
      ? 'h-[280px] rounded-xl border border-[var(--glass-border)] overflow-hidden'
      : 'h-[56px] rounded-lg border border-[var(--glass-border)] overflow-hidden'

  return (
    <div ref={containerRef} className={containerClass}>
      <canvas
        ref={canvasRef}
        className="block h-full w-full"
        onMouseDown={handleMouseDown}
        onMouseMove={handleCanvasMouseMove}
      />
    </div>
  )
}
