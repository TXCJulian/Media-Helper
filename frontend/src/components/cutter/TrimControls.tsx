import { useState, useEffect, useCallback } from 'react'

interface TrimControlsProps {
  inPoint: number
  outPoint: number
  duration: number
  onInPointChange: (time: number) => void
  onOutPointChange: (time: number) => void
}

/** Convert seconds to "HH:MM:SS.s" (hours only if >= 1h, always one decimal). */
function formatTime(seconds: number): string {
  const clamped = Math.max(0, seconds)
  const h = Math.floor(clamped / 3600)
  const m = Math.floor((clamped % 3600) / 60)
  const s = clamped % 60
  const sFixed = s.toFixed(1).padStart(4, '0')

  return h > 0
    ? `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${sFixed}`
    : `${String(m).padStart(2, '0')}:${sFixed}`
}

/** Parse a time string ("HH:MM:SS.ms", "MM:SS.ms", "SS.ms", etc.) into seconds. Returns NaN on failure. */
function parseTime(value: string): number {
  const trimmed = value.trim()
  if (!trimmed) return NaN

  const parts = trimmed.split(':')
  if (parts.length > 3) return NaN

  const nums = parts.map(Number)
  if (nums.some(isNaN)) return NaN

  let seconds: number
  if (parts.length === 3) {
    seconds = (nums[0] ?? 0) * 3600 + (nums[1] ?? 0) * 60 + (nums[2] ?? 0)
  } else if (parts.length === 2) {
    seconds = (nums[0] ?? 0) * 60 + (nums[1] ?? 0)
  } else {
    seconds = nums[0] ?? 0
  }

  return seconds < 0 ? NaN : seconds
}

const nudgeBtnClass =
  'flex h-[42px] w-[42px] shrink-0 cursor-pointer items-center justify-center rounded-[10px] border border-[var(--border)] bg-[var(--bg-input)] text-[0.75rem] font-semibold text-[var(--text-secondary)] transition-all duration-200 hover:border-[var(--glass-border-hover)] hover:bg-[rgba(255,255,255,0.07)] hover:text-[var(--text-primary)]'

export default function TrimControls({
  inPoint,
  outPoint,
  duration,
  onInPointChange,
  onOutPointChange,
}: TrimControlsProps) {
  const [inText, setInText] = useState(() => formatTime(inPoint))
  const [outText, setOutText] = useState(() => formatTime(outPoint))

  // Sync text fields when props change externally
  useEffect(() => {
    setInText(formatTime(inPoint))
  }, [inPoint])

  useEffect(() => {
    setOutText(formatTime(outPoint))
  }, [outPoint])

  const commitIn = useCallback(() => {
    const parsed = parseTime(inText)
    if (isNaN(parsed)) {
      setInText(formatTime(inPoint))
      return
    }
    const clamped = Math.min(Math.max(0, parsed), outPoint - 0.1)
    onInPointChange(Math.round(clamped * 10) / 10)
  }, [inText, inPoint, outPoint, onInPointChange])

  const commitOut = useCallback(() => {
    const parsed = parseTime(outText)
    if (isNaN(parsed)) {
      setOutText(formatTime(outPoint))
      return
    }
    const clamped = Math.max(Math.min(parsed, duration), inPoint + 0.1)
    onOutPointChange(Math.round(clamped * 10) / 10)
  }, [outText, outPoint, duration, inPoint, onOutPointChange])

  const nudgeIn = (delta: number) => {
    const next = Math.round(Math.max(0, Math.min(inPoint + delta, outPoint - 0.1)) * 10) / 10
    onInPointChange(next)
  }

  const nudgeOut = (delta: number) => {
    const next = Math.round(Math.max(inPoint + 0.1, Math.min(outPoint + delta, duration)) * 10) / 10
    onOutPointChange(next)
  }

  const handleReset = () => {
    onInPointChange(0)
    onOutPointChange(duration)
  }

  const trimmedDuration = Math.max(0, outPoint - inPoint)

  return (
    <div className="flex flex-col gap-3">
      {/* Text inputs with nudge buttons */}
      <div className="flex flex-wrap items-end gap-4">
        {/* In point */}
        <div className="min-w-[180px] flex-1">
          <label className="field-label">In</label>
          <div className="flex items-center gap-1">
            <button type="button" className={nudgeBtnClass} onClick={() => nudgeIn(-1)}>
              −1s
            </button>
            <input
              type="text"
              className="input-field input-emerald"
              value={inText}
              onChange={(e) => setInText(e.target.value)}
              onBlur={commitIn}
              onKeyDown={(e) => e.key === 'Enter' && commitIn()}
            />
            <button type="button" className={nudgeBtnClass} onClick={() => nudgeIn(1)}>
              +1s
            </button>
          </div>
        </div>

        {/* Out point */}
        <div className="min-w-[180px] flex-1">
          <label className="field-label">Out</label>
          <div className="flex items-center gap-1">
            <button type="button" className={nudgeBtnClass} onClick={() => nudgeOut(-1)}>
              −1s
            </button>
            <input
              type="text"
              className="input-field input-emerald"
              value={outText}
              onChange={(e) => setOutText(e.target.value)}
              onBlur={commitOut}
              onKeyDown={(e) => e.key === 'Enter' && commitOut()}
            />
            <button type="button" className={nudgeBtnClass} onClick={() => nudgeOut(1)}>
              +1s
            </button>
          </div>
        </div>

        {/* Trimmed duration display */}
        <div className="flex h-[42px] items-center text-[0.825rem] text-[var(--text-secondary)]">
          Duration:&nbsp;
          <span className="font-mono text-[var(--text-primary)]">
            {formatTime(trimmedDuration)}
          </span>
        </div>

        {/* Reset button */}
        <button
          type="button"
          onClick={handleReset}
          className="h-[42px] cursor-pointer rounded-[10px] border border-[var(--border)] bg-[var(--bg-input)] px-4 text-[0.825rem] text-[var(--text-secondary)] transition-all duration-250 hover:bg-[rgba(255,255,255,0.07)] hover:text-[var(--text-primary)]"
        >
          Reset
        </button>
      </div>

      {/* Range sliders */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <span className="w-8 text-[0.75rem] text-[var(--text-tertiary)]">In</span>
          <input
            type="range"
            min={0}
            max={duration}
            step={0.1}
            value={inPoint}
            onChange={(e) => {
              const val = parseFloat(e.target.value)
              if (val < outPoint - 0.1) onInPointChange(Math.round(val * 10) / 10)
            }}
            className="slider-emerald flex-1"
          />
        </div>
        <div className="flex items-center gap-3">
          <span className="w-8 text-[0.75rem] text-[var(--text-tertiary)]">Out</span>
          <input
            type="range"
            min={0}
            max={duration}
            step={0.1}
            value={outPoint}
            onChange={(e) => {
              const val = parseFloat(e.target.value)
              if (val > inPoint + 0.1) onOutPointChange(Math.round(val * 10) / 10)
            }}
            className="slider-emerald flex-1"
          />
        </div>
      </div>
    </div>
  )
}

export { formatTime, parseTime }
