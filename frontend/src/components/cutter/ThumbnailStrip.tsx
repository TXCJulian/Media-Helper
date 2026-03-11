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
  const dragRef = useRef<DragTarget>(null)
  const isDraggingRef = useRef(false)
  const [spriteImg, setSpriteImg] = useState<HTMLImageElement | null>(null)

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

  // Draw
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
        ctx.drawImage(
          spriteImg,
          i * frameW, 0, frameW, frameH,
          i * thumbW, 0, thumbW, cssH,
        )
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
      ctx.roundRect(xPos - HANDLE_TAB_WIDTH / 2, 0, HANDLE_TAB_WIDTH, HANDLE_TAB_HEIGHT, [0, 0, 3, 3])
      ctx.fill()
    }

    drawHandle(inX)
    drawHandle(outX)

    // Playhead
    const playX = timeToX(currentTime, cssW)
    ctx.strokeStyle = '#ffffff'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    ctx.moveTo(playX, 0)
    ctx.lineTo(playX, cssH)
    ctx.stroke()
  }, [spriteImg, duration, inPoint, outPoint, currentTime, count, timeToX])

  // Hit test
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

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const target = hitTest(e.clientX)
      if (target) {
        dragRef.current = target
        isDraggingRef.current = true
        e.preventDefault()
      } else {
        const canvas = canvasRef.current
        if (!canvas) return
        const rect = canvas.getBoundingClientRect()
        onSeek(xToTime(e.clientX - rect.left, rect))
      }
    },
    [hitTest, xToTime, onSeek],
  )

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDraggingRef.current || !dragRef.current) return
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const time = xToTime(e.clientX - rect.left, rect)

      if (dragRef.current === 'in') {
        onInPointChange(Math.max(0, Math.min(time, outPoint - 0.01)))
      } else {
        onOutPointChange(Math.max(inPoint + 0.01, Math.min(time, duration)))
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

  const handleCanvasMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current
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
    <div className="overflow-hidden rounded-lg border border-[var(--glass-border)]" style={{ height: STRIP_HEIGHT }}>
      <canvas
        ref={canvasRef}
        className="block h-full w-full"
        onMouseDown={handleMouseDown}
        onMouseMove={handleCanvasMouseMove}
      />
    </div>
  )
}
