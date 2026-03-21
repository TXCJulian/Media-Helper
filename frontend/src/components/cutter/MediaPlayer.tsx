import { useRef, useState, useEffect, useCallback } from 'react'
import WaveformBar from '@/components/cutter/WaveformBar'
import ThumbnailStrip from '@/components/cutter/ThumbnailStrip'

interface MediaPlayerProps {
  streamUrl: string
  audioUrl?: string
  isVideo: boolean
  peaks: number[]
  duration: number
  sourceAspectRatio?: string | null
  videoWidth?: number | null
  videoHeight?: number | null
  inPoint: number
  outPoint: number
  onInPointChange: (time: number) => void
  onOutPointChange: (time: number) => void
  thumbnailUrl?: string
  needsTranscoding?: boolean
  transcodePercent?: number
  transcodeEtaSeconds?: number | null
  transcodeState?: 'idle' | 'running' | 'done' | 'error'
  transcodeMessage?: string
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return [h, m, s].map((v) => String(v).padStart(2, '0')).join(':')
}

function formatEta(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60

  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`
  return `${s}s`
}

const MEDIA_ERROR_CODE_LABELS = ['', 'ABORTED', 'NETWORK', 'DECODE', 'SRC_NOT_SUPPORTED']

const MEDIA_ERROR_HINTS: Array<{ match: RegExp; hint: string }> = [
  {
    match: /DEMUXER_ERROR_NO_SUPPORTED_STREAMS|no supported streams/i,
    hint: 'This file likely uses a video or audio codec your browser cannot decode. Try enabling Transcoded Preview.',
  },
  {
    match: /SRC_NOT_SUPPORTED|NotSupportedError/i,
    hint: 'The browser cannot play this media source directly. Try enabling Transcoded Preview.',
  },
  {
    match: /DECODE|decode|corrupt|invalid data/i,
    hint: 'The media stream could not be decoded. The file may be damaged or use an unsupported codec profile.',
  },
  {
    match: /NETWORK|ERR_|Failed to fetch/i,
    hint: 'The stream could not be loaded due to a network or server issue. Retry in a moment.',
  },
]

function getFriendlyMediaError(technical: string, code: number): string {
  for (const entry of MEDIA_ERROR_HINTS) {
    if (entry.match.test(technical)) return entry.hint
  }

  if (code === 4) {
    return 'This media format is not supported by your browser. Try enabling Transcoded Preview.'
  }
  if (code === 3) {
    return 'The browser could not decode the media stream. The codec or profile may not be supported.'
  }
  if (code === 2) {
    return 'A network error interrupted media playback. Please retry.'
  }
  if (code === 1) {
    return 'Playback was aborted before the stream became ready.'
  }

  return 'Playback failed for this media source.'
}

export default function MediaPlayer({
  streamUrl,
  audioUrl,
  isVideo,
  peaks,
  duration,
  sourceAspectRatio,
  videoWidth,
  videoHeight,
  inPoint,
  outPoint,
  onInPointChange,
  onOutPointChange,
  thumbnailUrl,
  needsTranscoding,
  transcodePercent,
  transcodeEtaSeconds,
  transcodeState,
  transcodeMessage,
}: MediaPlayerProps) {
  const END_TOLERANCE_SECONDS = 0.05
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const volumeControlRef = useRef<HTMLDivElement | null>(null)
  const isDualMode = !!audioUrl
  const rafRef = useRef<number>(0)
  const inPointRef = useRef(inPoint)
  const outPointRef = useRef(outPoint)
  const [currentTime, setCurrentTime] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [volume, setVolume] = useState(() => {
    const saved = localStorage.getItem('cutter-volume')
    return saved != null ? parseFloat(saved) : 1
  })
  const [muted, setMuted] = useState(false)
  const [isMediaReady, setIsMediaReady] = useState(!needsTranscoding)
  const [loadedAspectRatio, setLoadedAspectRatio] = useState<string | null>(null)
  // Backend is still producing the file — don't let the browser request it yet
  const isTranscodeRunning = needsTranscoding && transcodeState != null && transcodeState !== 'done'
  const isTranscoding = needsTranscoding && (!isMediaReady || isTranscodeRunning)
  const fallbackAspectRatio =
    sourceAspectRatio && sourceAspectRatio.trim().length > 0
      ? sourceAspectRatio
      : videoWidth != null && videoHeight != null && videoWidth > 0 && videoHeight > 0
        ? `${videoWidth} / ${videoHeight}`
        : '16 / 9'
  const videoAspectRatio = loadedAspectRatio ?? fallbackAspectRatio

  useEffect(() => {
    inPointRef.current = inPoint
    outPointRef.current = outPoint
  }, [inPoint, outPoint])

  const enforceTrimBounds = useCallback((el: HTMLVideoElement | HTMLAudioElement): boolean => {
    const start = inPointRef.current
    const end = outPointRef.current

    if (el.currentTime >= end - END_TOLERANCE_SECONDS) {
      el.currentTime = start
      setCurrentTime(start)
      return true
    }

    return false
  }, [])

  // ── Cut-preview RAF loop ──────────────────────────────────────
  const startLoop = useCallback(() => {
    if (rafRef.current) return

    const tick = () => {
      const el = mediaRef.current
      if (!el) return
      if (el.paused) {
        rafRef.current = 0
        return
      }
      enforceTrimBounds(el)
      // Keep separate audio element in sync with video
      const audioEl = audioRef.current
      if (audioEl) {
        const drift = Math.abs(audioEl.currentTime - el.currentTime)
        if (drift > 0.1) audioEl.currentTime = el.currentTime
      }
      setCurrentTime(el.currentTime)
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }, [enforceTrimBounds])

  const stopLoop = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
    }
  }, [])

  // Clean up RAF on unmount
  useEffect(() => stopLoop, [stopLoop])

  // Sync volume to media element (or audio element in dual mode)
  useEffect(() => {
    if (isDualMode) {
      if (mediaRef.current) mediaRef.current.muted = true
      if (audioRef.current) audioRef.current.volume = muted ? 0 : volume
    } else {
      if (mediaRef.current) {
        mediaRef.current.muted = false
        mediaRef.current.volume = muted ? 0 : volume
      }
    }
  }, [volume, muted, isDualMode])

  // Persist volume to localStorage
  useEffect(() => {
    localStorage.setItem('cutter-volume', String(volume))
  }, [volume])

  const adjustVolumeByWheel = useCallback((deltaY: number) => {
    const delta = deltaY < 0 ? 0.1 : -0.1
    setVolume((v) => {
      const next = Math.max(0, Math.min(1, v + delta))
      if (next > 0) setMuted(false)
      return next
    })
  }, [])

  // Use a non-passive native listener so wheel changes volume without page scroll.
  useEffect(() => {
    const el = volumeControlRef.current
    if (!el) return

    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      event.stopPropagation()
      adjustVolumeByWheel(event.deltaY)
    }

    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      el.removeEventListener('wheel', onWheel)
    }
  }, [adjustVolumeByWheel])

  // Reset ready/error state and force media reload when stream URL changes.
  // Skip loading while the backend is still transcoding to avoid premature
  // requests that block on the server.
  useEffect(() => {
    setMediaError('')
    setLoadedAspectRatio(null)
    if (needsTranscoding) setIsMediaReady(false)
    if (isTranscodeRunning && !isDualMode) return
    const el = mediaRef.current
    if (el) {
      el.pause()
      setIsPlaying(false)
      stopLoop()
      el.load()
      setCurrentTime(0)
    }
  }, [streamUrl, needsTranscoding, isTranscodeRunning, isDualMode, stopLoop])

  // Sync separate audio element when audioUrl changes (track switch)
  useEffect(() => {
    const audioEl = audioRef.current
    const videoEl = mediaRef.current
    if (!audioEl || !audioUrl) return
    audioEl.load()
    if (videoEl) {
      audioEl.currentTime = videoEl.currentTime
      if (!videoEl.paused) audioEl.play().catch(() => {})
    }
  }, [audioUrl])

  // Reset cursor to 00:00 after audio transcode completes to prevent
  // video/audio desync from playback during the transcode.
  const prevTranscodeState = useRef(transcodeState)
  useEffect(() => {
    const prev = prevTranscodeState.current
    prevTranscodeState.current = transcodeState
    if (prev != null && prev !== 'done' && transcodeState === 'done') {
      const el = mediaRef.current
      if (el) {
        el.pause()
        el.currentTime = 0
        setIsPlaying(false)
        stopLoop()
      }
      if (audioRef.current) audioRef.current.currentTime = 0
      setCurrentTime(0)
    }
  }, [transcodeState, stopLoop])

  // ── Play / Pause toggle ───────────────────────────────────────
  const togglePlay = useCallback(() => {
    const el = mediaRef.current
    if (!el) return

    const audioEl = audioRef.current

    if (isPlaying) {
      el.pause()
      audioEl?.pause()
      setIsPlaying(false)
      stopLoop()
    } else {
      // Only reset to inPoint if outside trim range or playback finished
      if (
        el.currentTime < inPoint ||
        el.currentTime >= outPoint - END_TOLERANCE_SECONDS ||
        el.ended
      ) {
        el.currentTime = inPoint
        if (audioEl) audioEl.currentTime = inPoint
        setCurrentTime(inPoint)
      }
      if (audioEl) {
        audioEl.currentTime = el.currentTime
        audioEl.play().catch(() => {})
      }
      const playPromise = el.play()
      if (playPromise && typeof playPromise.then === 'function') {
        playPromise
          .then(() => {
            setIsPlaying(true)
            startLoop()
          })
          .catch(() => {
            setIsPlaying(false)
            stopLoop()
          })
      } else {
        setIsPlaying(true)
        startLoop()
      }
    }
  }, [isPlaying, inPoint, outPoint, startLoop, stopLoop])

  // ── Always seek to inPoint when it changes ──────────────────
  useEffect(() => {
    const el = mediaRef.current
    if (!el) return
    el.currentTime = inPoint
    if (audioRef.current) audioRef.current.currentTime = inPoint
    setCurrentTime(inPoint)
  }, [inPoint])

  // ── Sync when media ends or pauses externally ─────────────────
  const handlePause = useCallback(() => {
    audioRef.current?.pause()
    setIsPlaying(false)
    stopLoop()
  }, [stopLoop])

  const handleTimeUpdate = useCallback(() => {
    const el = mediaRef.current
    if (!el) return
    enforceTrimBounds(el)
    // While playing, RAF already updates currentTime at frame rate.
    if (!isPlaying) {
      setCurrentTime(el.currentTime)
    }
  }, [enforceTrimBounds, isPlaying])

  const handleLoadedMetadata = useCallback(() => {
    const el = mediaRef.current
    if (!(el instanceof HTMLVideoElement)) return
    if (el.videoWidth > 0 && el.videoHeight > 0) {
      setLoadedAspectRatio(`${el.videoWidth} / ${el.videoHeight}`)
    }
  }, [])

  const handleEnded = useCallback(() => {
    const el = mediaRef.current
    if (!el) return

    const start = inPointRef.current
    el.currentTime = start
    if (audioRef.current) {
      audioRef.current.currentTime = start
      audioRef.current.play().catch(() => {})
    }
    setCurrentTime(start)

    const playPromise = el.play()
    if (playPromise && typeof playPromise.then === 'function') {
      playPromise
        .then(() => {
          setIsPlaying(true)
          startLoop()
        })
        .catch(() => {
          setIsPlaying(false)
          stopLoop()
        })
    } else {
      setIsPlaying(true)
      startLoop()
    }
  }, [startLoop, stopLoop])

  const [mediaError, setMediaError] = useState<string>('')

  const isPreparingPostTranscode = isTranscoding && transcodeState === 'done'
  const transcodingProgress = Number.isFinite(transcodePercent)
    ? Math.max(0, Math.min(100, transcodePercent ?? 0))
    : 0
  const transcodingPercentLabel = `${transcodingProgress.toFixed(1)}%`
  const transcodingEtaLabel =
    transcodeEtaSeconds != null && Number.isFinite(transcodeEtaSeconds)
      ? formatEta(transcodeEtaSeconds)
      : null
  const transcodingTitle = isPreparingPostTranscode ? 'Preparing stream' : 'Transcoding preview'
  const transcodingDetailMessage =
    transcodeMessage?.trim() || 'Generating browser-compatible preview'

  const handleMediaError = useCallback(() => {
    const el = mediaRef.current
    if (!el) return
    const err = el.error
    const msg = err
      ? (() => {
          const technical = err.message || MEDIA_ERROR_CODE_LABELS[err.code] || 'unknown'
          const friendly = getFriendlyMediaError(technical, err.code)
          return `${friendly}\nMedia error ${err.code}: ${technical}`
        })()
      : 'Playback failed for an unknown reason.'
    setMediaError(msg)
    setIsMediaReady(true)
    setIsPlaying(false)
    stopLoop()
    console.error('[MediaPlayer]', msg, 'src:', streamUrl)
  }, [streamUrl, stopLoop])

  // ── Seek from WaveformBar ─────────────────────────────────────
  const handleSeek = useCallback((time: number) => {
    const el = mediaRef.current
    if (!el) return
    el.currentTime = time
    if (audioRef.current) audioRef.current.currentTime = time
    setCurrentTime(time)
  }, [])

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
      <div ref={volumeControlRef} className="ml-auto flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setMuted((m) => !m)}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-white/60 transition hover:text-white/90"
          aria-label={muted ? 'Unmute' : 'Mute'}
        >
          {muted || volume === 0 ? (
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M11 5L6 9H2v6h4l5 4V5z" />
              <line x1="23" y1="9" x2="17" y2="15" />
              <line x1="17" y1="9" x2="23" y2="15" />
            </svg>
          ) : volume < 0.5 ? (
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M11 5L6 9H2v6h4l5 4V5z" />
              <path d="M15.54 8.46a5 5 0 010 7.07" />
            </svg>
          ) : (
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M11 5L6 9H2v6h4l5 4V5z" />
              <path d="M15.54 8.46a5 5 0 010 7.07" />
              <path d="M19.07 4.93a10 10 0 010 14.14" />
            </svg>
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
            src={isTranscodeRunning && !isDualMode ? undefined : streamUrl}
            className="w-full cursor-pointer rounded-xl bg-black"
            style={{ aspectRatio: videoAspectRatio }}
            onClick={isTranscoding ? undefined : togglePlay}
            onPause={handlePause}
            onTimeUpdate={handleTimeUpdate}
            onEnded={handleEnded}
            onLoadedMetadata={handleLoadedMetadata}
            onError={handleMediaError}
            onCanPlay={() => {
              setMediaError('')
              setIsMediaReady(true)
            }}
            preload="none"
            playsInline
          />
          {isDualMode && (
            <audio
              ref={audioRef}
              src={audioUrl}
              preload="auto"
              style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
            />
          )}
          {isTranscoding && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-xl bg-black/70">
              <span className="spinner-md" />
              <div className="w-[min(84%,22rem)] rounded-xl border border-white/15 bg-black/45 px-3.5 py-3 backdrop-blur-sm">
                <p className="text-center text-[0.78rem] font-medium uppercase tracking-[0.12em] text-white/55">
                  {transcodingTitle}
                </p>
                <div className="mt-2 flex items-center justify-center gap-2 text-sm">
                  {!isPreparingPostTranscode && (
                    <span className="rounded-md border border-emerald-300/25 bg-emerald-300/10 px-2 py-1 font-mono font-semibold text-emerald-200">
                      {transcodingPercentLabel}
                    </span>
                  )}
                  {!isPreparingPostTranscode && transcodingEtaLabel && (
                    <span className="rounded-md border border-white/20 bg-white/8 px-2 py-1 font-mono text-white/80">
                      ETA {transcodingEtaLabel}
                    </span>
                  )}
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/15">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-emerald-300 via-teal-300 to-emerald-400 transition-[width] duration-300 ease-out"
                    style={{ width: `${transcodingProgress}%` }}
                  />
                </div>
                <p className="mt-2 text-center text-[0.7rem] text-white/65">
                  {transcodingDetailMessage}
                </p>
              </div>
            </div>
          )}
        </div>
        {mediaError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs whitespace-pre-line text-red-300">
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
        src={isTranscodeRunning ? undefined : streamUrl}
        preload="none"
        onPause={handlePause}
        onTimeUpdate={handleTimeUpdate}
        onEnded={handleEnded}
        onError={handleMediaError}
        onCanPlay={() => {
          setMediaError('')
          setIsMediaReady(true)
        }}
        style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
      />
      {isTranscoding && (
        <div className="flex items-center justify-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--bg-input)] px-4 py-7">
          <span className="spinner-md" />
          <div className="w-full max-w-xs rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5">
            <p className="text-[0.73rem] font-medium uppercase tracking-[0.11em] text-[var(--text-secondary)]">
              {transcodingTitle}
            </p>
            <div className="mt-2 flex items-center gap-2 text-xs">
              {!isPreparingPostTranscode && (
                <span className="rounded-md border border-emerald-300/25 bg-emerald-300/10 px-2 py-1 font-mono font-semibold text-emerald-200">
                  {transcodingPercentLabel}
                </span>
              )}
              {!isPreparingPostTranscode && transcodingEtaLabel && (
                <span className="rounded-md border border-white/15 bg-white/5 px-2 py-1 font-mono text-white/75">
                  ETA {transcodingEtaLabel}
                </span>
              )}
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/12">
              <div
                className="h-full rounded-full bg-gradient-to-r from-emerald-300 via-teal-300 to-cyan-300 transition-[width] duration-300 ease-out"
                style={{ width: `${transcodingProgress}%` }}
              />
            </div>
            <p className="mt-2 text-[0.68rem] text-[var(--text-tertiary)]">
              {transcodingDetailMessage}
            </p>
          </div>
        </div>
      )}
      {mediaError && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs whitespace-pre-line text-red-300">
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
