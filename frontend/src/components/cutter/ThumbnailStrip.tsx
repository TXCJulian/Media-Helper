import { useRef, useEffect, useCallback, useState } from 'react'

interface ThumbnailStripProps {
  thumbnailUrl: string
  duration: number
  inPoint: number
  outPoint: number
  currentTime: number
  onInPointChange: (time: number) => void
  onOutPointChange: (time: number) => void
  onSeek: (time: number) => void
  count?: number
}

type DragTarget = 'in' | 'out' | null

const HANDLE_HIT_PX = 8
const HANDLE_TAB_WIDTH = 6
const HANDLE_TAB_HEIGHT = 14
const STRIP_HEIGHT = 80
const ACCENT = '#34d399'
const PLAYHEAD_OUTLINE = 'rgba(0, 0, 0, 0.75)'
const PLAYHEAD_CORE = '#ffffff'

export default function ThumbnailStrip({
  thumbnailUrl,
  duration,
  inPoint,
  outPoint,
  currentTime,
  onInPointChange,
  onOutPointChange,
  onSeek,
  count = 30,
}: ThumbnailStripProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const overlayRef = useRef<HTMLCanvasElement>(null)
  const overlaySizeRef = useRef<{ width: number; height: number; dpr: number } | null>(null)
  const dragRef = useRef<DragTarget>(null)
  const isDraggingRef = useRef(false)
  const inPointRef = useRef(inPoint)
  const outPointRef = useRef(outPoint)
  const [resizeKey, setResizeKey] = useState(0)
  const [spriteImg, setSpriteImg] = useState<HTMLImageElement | null>(null)

  useEffect(() => {
    inPointRef.current = inPoint
  }, [inPoint])
  useEffect(() => {
    outPointRef.current = outPoint
  }, [outPoint])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const observer = new ResizeObserver(() => setResizeKey((n) => n + 1))
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  // Load sprite sheet
  useEffect(() => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => setSpriteImg(img)
    img.src = thumbnailUrl
    return () => {
      img.onload = null
    }
  }, [thumbnailUrl])

  const timeToX = useCallback(
    (time: number, width: number) => (duration > 0 ? (time / duration) * width : 0),
    [duration],
  )

  const xToTime = useCallback(
    (x: number, rect: DOMRect) => {
      if (duration <= 0) return 0
      return Math.max(0, Math.min(1, x / rect.width)) * duration
    },
    [duration],
  )

  // ── Main canvas: thumbnails + trim overlay + handles ──────────
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
    ctx.roundRect(0, 0, cssW, cssH, 8)
    ctx.fill()

    // Draw thumbnails from sprite sheet
    if (spriteImg && spriteImg.naturalWidth > 0) {
      const frameW = spriteImg.naturalWidth / count
      const frameH = spriteImg.naturalHeight
      const thumbW = cssW / count

      for (let i = 0; i < count; i++) {
        ctx.drawImage(spriteImg, i * frameW, 0, frameW, frameH, i * thumbW, 0, thumbW, cssH)
      }
    }

    // Dim regions outside trim
    const inX = timeToX(inPoint, cssW)
    const outX = timeToX(outPoint, cssW)

    ctx.fillStyle = 'rgba(0, 0, 0, 0.5)'
    if (inX > 0) ctx.fillRect(0, 0, inX, cssH)
    if (outX < cssW) ctx.fillRect(outX, 0, cssW - outX, cssH)

    // Active region border
    ctx.strokeStyle = ACCENT
    ctx.lineWidth = 2
    ctx.strokeRect(inX, 0, outX - inX, cssH)

    // Handles
    const drawHandle = (xPos: number) => {
      ctx.strokeStyle = ACCENT
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.moveTo(xPos, 0)
      ctx.lineTo(xPos, cssH)
      ctx.stroke()

      ctx.fillStyle = ACCENT
      ctx.beginPath()
      ctx.roundRect(
        xPos - HANDLE_TAB_WIDTH / 2,
        0,
        HANDLE_TAB_WIDTH,
        HANDLE_TAB_HEIGHT,
        [0, 0, 3, 3],
      )
      ctx.fill()
    }

    drawHandle(inX)
    drawHandle(outX)
  }, [spriteImg, duration, inPoint, outPoint, count, timeToX, resizeKey])

  // ── Overlay canvas: playhead only (redraws at 60fps during playback) ──
  useEffect(() => {
    const overlay = overlayRef.current
    if (!overlay) return
    const ctx = overlay.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const rect = overlay.getBoundingClientRect()
    const cssW = rect.width
    const cssH = rect.height
    if (cssW <= 0 || cssH <= 0) return

    const width = Math.max(1, Math.round(cssW * dpr))
    const height = Math.max(1, Math.round(cssH * dpr))
    const prev = overlaySizeRef.current
    if (!prev || prev.width !== width || prev.height !== height || prev.dpr !== dpr) {
      overlay.width = width
      overlay.height = height
      overlaySizeRef.current = { width, height, dpr }
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    ctx.clearRect(0, 0, cssW, cssH)

    const playX = timeToX(currentTime, cssW)

    // Draw high-contrast playhead so it remains visible on bright thumbnails.
    ctx.strokeStyle = PLAYHEAD_OUTLINE
    ctx.lineWidth = 3.5
    ctx.beginPath()
    ctx.moveTo(playX, 0)
    ctx.lineTo(playX, cssH)
    ctx.stroke()

    ctx.strokeStyle = PLAYHEAD_CORE
    ctx.lineWidth = 1.5
    ctx.beginPath()
    ctx.moveTo(playX, 0)
    ctx.lineTo(playX, cssH)
    ctx.stroke()

    // Small caps improve visibility when the line overlaps edges/highlights.
    ctx.fillStyle = PLAYHEAD_OUTLINE
    ctx.beginPath()
    ctx.roundRect(playX - 4, 0, 8, 6, [0, 0, 4, 4])
    ctx.roundRect(playX - 4, cssH - 6, 8, 6, [4, 4, 0, 0])
    ctx.fill()

    ctx.fillStyle = PLAYHEAD_CORE
    ctx.beginPath()
    ctx.roundRect(playX - 2, 0, 4, 4, [0, 0, 2, 2])
    ctx.roundRect(playX - 2, cssH - 4, 4, 4, [2, 2, 0, 0])
    ctx.fill()
  }, [currentTime, timeToX, resizeKey])

  // Hit test
  const hitTest = useCallback(
    (clientX: number): DragTarget => {
      const canvas = overlayRef.current
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

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      const target = hitTest(e.clientX)
      if (target) {
        dragRef.current = target
        isDraggingRef.current = true
        e.preventDefault()

        // Register window listeners only during drag
        const handlePointerMove = (ev: PointerEvent) => {
          const canvas = overlayRef.current
          if (!canvas) return
          const rect = canvas.getBoundingClientRect()
          const time = xToTime(ev.clientX - rect.left, rect)

          if (dragRef.current === 'in') {
            onInPointChange(Math.max(0, Math.min(time, outPointRef.current - 0.01)))
          } else {
            onOutPointChange(Math.max(inPointRef.current + 0.01, Math.min(time, duration)))
          }
        }

        const handlePointerUp = () => {
          dragRef.current = null
          isDraggingRef.current = false
          window.removeEventListener('pointermove', handlePointerMove)
          window.removeEventListener('pointerup', handlePointerUp)
        }

        window.addEventListener('pointermove', handlePointerMove)
        window.addEventListener('pointerup', handlePointerUp)
      } else {
        // Click to seek (clamped to trim range)
        const canvas = overlayRef.current
        if (!canvas) return
        const rect = canvas.getBoundingClientRect()
        const time = xToTime(e.clientX - rect.left, rect)
        onSeek(Math.max(inPoint, Math.min(time, outPoint)))
      }
    },
    [hitTest, xToTime, onSeek, inPoint, outPoint, duration, onInPointChange, onOutPointChange],
  )

  const handleCanvasPointerMove = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      const canvas = overlayRef.current
      if (!canvas) return
      if (isDraggingRef.current) {
        canvas.style.cursor = 'grabbing'
        return
      }
      canvas.style.cursor = hitTest(e.clientX) ? 'grab' : 'pointer'
    },
    [hitTest],
  )

  return (
    <div
      className="relative overflow-hidden rounded-lg border border-[var(--glass-border)]"
      style={{ height: STRIP_HEIGHT }}
    >
      <canvas ref={canvasRef} className="absolute inset-0 block h-full w-full" />
      <canvas
        ref={overlayRef}
        className="absolute inset-0 block h-full w-full"
        style={{ touchAction: 'none' }}
        onPointerDown={handlePointerDown}
        onPointerMove={handleCanvasPointerMove}
      />
    </div>
  )
}
