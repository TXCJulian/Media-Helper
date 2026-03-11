import { useRef, useState, useEffect, useCallback } from 'react'
import WaveformBar from '@/components/cutter/WaveformBar'
import ThumbnailStrip from '@/components/cutter/ThumbnailStrip'

interface MediaPlayerProps {
  streamUrl: string
  isVideo: boolean
  peaks: number[]
  duration: number
  inPoint: number
  outPoint: number
  onInPointChange: (time: number) => void
  onOutPointChange: (time: number) => void
  thumbnailUrl?: string
  needsTranscoding?: boolean
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
  thumbnailUrl,
  needsTranscoding,
}: MediaPlayerProps) {
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null)
  const rafRef = useRef<number>(0)
  const [currentTime, setCurrentTime] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [volume, setVolume] = useState(() => {
    const saved = localStorage.getItem('cutter-volume')
    return saved != null ? parseFloat(saved) : 1
  })
  const [muted, setMuted] = useState(false)
  const [isMediaReady, setIsMediaReady] = useState(!needsTranscoding)
  const isTranscoding = needsTranscoding && !isMediaReady

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

  // Sync volume to media element
  useEffect(() => {
    if (mediaRef.current) {
      mediaRef.current.volume = muted ? 0 : volume
    }
  }, [volume, muted])

  // Persist volume to localStorage
  useEffect(() => {
    localStorage.setItem('cutter-volume', String(volume))
  }, [volume])

  // Reset ready/error state when stream URL changes
  useEffect(() => {
    setMediaError('')
    if (needsTranscoding) setIsMediaReady(false)
  }, [streamUrl, needsTranscoding])

  // ── Play / Pause toggle ───────────────────────────────────────
  const togglePlay = useCallback(() => {
    const el = mediaRef.current
    if (!el) return

    if (isPlaying) {
      el.pause()
      setIsPlaying(false)
      stopLoop()
    } else {
      // Only reset to inPoint if outside trim range or playback finished
      if (el.currentTime < inPoint || el.currentTime >= outPoint) {
        el.currentTime = inPoint
        setCurrentTime(inPoint)
      }
      el.play()
      setIsPlaying(true)
      startLoop()
    }
  }, [isPlaying, inPoint, outPoint, startLoop, stopLoop])

  // ── Clamp when inPoint moves ahead of current position ─────────
  useEffect(() => {
    const el = mediaRef.current
    if (!el || !isPlaying) return
    if (el.currentTime < inPoint) {
      el.currentTime = inPoint
      setCurrentTime(inPoint)
    }
  }, [inPoint, isPlaying])

  // ── Sync when media ends or pauses externally ─────────────────
  const handlePause = useCallback(() => {
    setIsPlaying(false)
    stopLoop()
  }, [stopLoop])

  const handleTimeUpdate = useCallback(() => {
    const el = mediaRef.current
    if (el) setCurrentTime(el.currentTime)
  }, [])

  const [mediaError, setMediaError] = useState<string>('')

  const handleMediaError = useCallback(() => {
    const el = mediaRef.current
    if (!el) return
    const err = el.error
    const msg = err
      ? `Media error ${err.code}: ${err.message || ['', 'ABORTED', 'NETWORK', 'DECODE', 'SRC_NOT_SUPPORTED'][err.code] || 'unknown'}`
      : 'Unknown media error'
    setMediaError(msg)
    console.error('[MediaPlayer]', msg, 'src:', streamUrl)
  }, [streamUrl])

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
      <div className="ml-auto flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setMuted((m) => !m)}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-white/60 transition hover:text-white/90"
          aria-label={muted ? 'Unmute' : 'Mute'}
        >
          {muted || volume === 0 ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 5L6 9H2v6h4l5 4V5z"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>
          ) : volume < 0.5 ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M15.54 8.46a5 5 0 010 7.07"/></svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M15.54 8.46a5 5 0 010 7.07"/><path d="M19.07 4.93a10 10 0 010 14.14"/></svg>
          )}
        </button>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={muted ? 0 : volume}
          onChange={(e) => {
            const v = parseFloat(e.target.value)
            setVolume(v)
            if (v > 0) setMuted(false)
          }}
          className="h-1 w-16 cursor-pointer appearance-none rounded-full bg-white/10
                     [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3
                     [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
                     [&::-webkit-slider-thumb]:bg-emerald-400"
          aria-label="Volume"
        />
      </div>
    </div>
  )

  // ── Video mode ────────────────────────────────────────────────
  if (isVideo) {
    return (
      <div className="flex flex-col gap-2">
        <div className="relative">
          <video
            ref={mediaRef as React.RefObject<HTMLVideoElement>}
            src={streamUrl}
            className="w-full cursor-pointer rounded-xl bg-black"
            style={{ aspectRatio: '16 / 9' }}
            onClick={isTranscoding ? undefined : togglePlay}
            onPause={handlePause}
            onTimeUpdate={handleTimeUpdate}
            onError={handleMediaError}
            onCanPlay={() => setIsMediaReady(true)}
            preload="metadata"
            playsInline
          />
          {isTranscoding && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-xl bg-black/70">
              <span className="spinner-md" />
              <span className="text-[0.8rem] text-white/60">Transcoding for preview...</span>
            </div>
          )}
        </div>
        {mediaError && (
          <div className="rounded-lg bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs text-red-300">
            {mediaError}
          </div>
        )}
        {controls}
        {thumbnailUrl ? (
          <ThumbnailStrip
            thumbnailUrl={thumbnailUrl}
            duration={duration}
            inPoint={inPoint}
            outPoint={outPoint}
            currentTime={currentTime}
            onInPointChange={onInPointChange}
            onOutPointChange={onOutPointChange}
            onSeek={handleSeek}
          />
        ) : (
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
        )}
      </div>
    )
  }

  // ── Audio mode ────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-2">
      <audio
        ref={mediaRef as React.RefObject<HTMLAudioElement>}
        src={streamUrl}
        preload="metadata"
        onPause={handlePause}
        onTimeUpdate={handleTimeUpdate}
        onError={handleMediaError}
        onCanPlay={() => setIsMediaReady(true)}
        style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
      />
      {isTranscoding && (
        <div className="flex items-center justify-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--bg-input)] py-8">
          <span className="spinner-md" />
          <span className="text-[0.8rem] text-[var(--text-tertiary)]">Transcoding for preview...</span>
        </div>
      )}
      {mediaError && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs text-red-300">
          {mediaError}
        </div>
      )}
      {thumbnailUrl ? (
        <ThumbnailStrip
          thumbnailUrl={thumbnailUrl}
          duration={duration}
          inPoint={inPoint}
          outPoint={outPoint}
          currentTime={currentTime}
          onInPointChange={onInPointChange}
          onOutPointChange={onOutPointChange}
          onSeek={handleSeek}
        />
      ) : (
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
      )}
      {controls}
    </div>
  )
}
