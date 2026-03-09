import { useRef, useState, useEffect, useCallback } from 'react'
import WaveformBar from '@/components/cutter/WaveformBar'

interface MediaPlayerProps {
  streamUrl: string
  isVideo: boolean
  peaks: number[]
  duration: number
  inPoint: number
  outPoint: number
  onInPointChange: (time: number) => void
  onOutPointChange: (time: number) => void
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return [h, m, s].map((v) => String(v).padStart(2, '0')).join(':')
}

export default function MediaPlayer({
  streamUrl,
  isVideo,
  peaks,
  duration,
  inPoint,
  outPoint,
  onInPointChange,
  onOutPointChange,
}: MediaPlayerProps) {
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null)
  const rafRef = useRef<number>(0)
  const [currentTime, setCurrentTime] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)

  // ── Cut-preview RAF loop ──────────────────────────────────────
  const startLoop = useCallback(() => {
    const tick = () => {
      const el = mediaRef.current
      if (!el) return
      setCurrentTime(el.currentTime)
      if (el.currentTime >= outPoint) {
        el.pause()
        setIsPlaying(false)
        return
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }, [outPoint])

  const stopLoop = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
    }
  }, [])

  // Clean up RAF on unmount
  useEffect(() => stopLoop, [stopLoop])

  // ── Play / Pause toggle ───────────────────────────────────────
  const togglePlay = useCallback(() => {
    const el = mediaRef.current
    if (!el) return

    if (isPlaying) {
      el.pause()
      setIsPlaying(false)
      stopLoop()
    } else {
      el.currentTime = inPoint
      setCurrentTime(inPoint)
      el.play()
      setIsPlaying(true)
      startLoop()
    }
  }, [isPlaying, inPoint, startLoop, stopLoop])

  // ── Restart from new inPoint when trim changes during playback ─
  useEffect(() => {
    const el = mediaRef.current
    if (!el || !isPlaying) return
    el.currentTime = inPoint
    setCurrentTime(inPoint)
  }, [inPoint, outPoint, isPlaying])

  // ── Sync when media ends or pauses externally ─────────────────
  const handlePause = useCallback(() => {
    setIsPlaying(false)
    stopLoop()
  }, [stopLoop])

  const handleTimeUpdate = useCallback(() => {
    const el = mediaRef.current
    if (el) setCurrentTime(el.currentTime)
  }, [])

  // ── Seek from WaveformBar ─────────────────────────────────────
  const handleSeek = useCallback(
    (time: number) => {
      const el = mediaRef.current
      if (!el) return
      el.currentTime = time
      setCurrentTime(time)
    },
    [],
  )

  // ── Controls bar ──────────────────────────────────────────────
  const controls = (
    <div className="flex items-center gap-3 px-1 py-2">
      <button
        type="button"
        onClick={togglePlay}
        className="flex h-9 w-9 items-center justify-center rounded-lg
                   border border-[var(--glass-border)] bg-[var(--glass-bg)]
                   text-sm text-white/80 backdrop-blur-sm transition
                   hover:border-emerald-400/40 hover:text-emerald-300"
        aria-label={isPlaying ? 'Pause' : 'Play'}
      >
        {isPlaying ? '\u23F8' : '\u25B6'}
      </button>
      <span className="font-mono text-xs text-white/60">
        {formatTime(currentTime)} / {formatTime(duration)}
      </span>
    </div>
  )

  // ── Video mode ────────────────────────────────────────────────
  if (isVideo) {
    return (
      <div className="flex flex-col gap-2">
        <video
          ref={mediaRef as React.RefObject<HTMLVideoElement>}
          src={streamUrl}
          className="w-full rounded-xl bg-black"
          style={{ aspectRatio: '16 / 9' }}
          onPause={handlePause}
          onTimeUpdate={handleTimeUpdate}
          playsInline
        />
        {controls}
        <WaveformBar
          peaks={peaks}
          duration={duration}
          inPoint={inPoint}
          outPoint={outPoint}
          currentTime={currentTime}
          onInPointChange={onInPointChange}
          onOutPointChange={onOutPointChange}
          onSeek={handleSeek}
          size="small"
        />
      </div>
    )
  }

  // ── Audio mode ────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-2">
      <audio
        ref={mediaRef as React.RefObject<HTMLAudioElement>}
        src={streamUrl}
        className="hidden"
        onPause={handlePause}
        onTimeUpdate={handleTimeUpdate}
      />
      <WaveformBar
        peaks={peaks}
        duration={duration}
        inPoint={inPoint}
        outPoint={outPoint}
        currentTime={currentTime}
        onInPointChange={onInPointChange}
        onOutPointChange={onOutPointChange}
        onSeek={handleSeek}
        size="large"
      />
      {controls}
    </div>
  )
}
