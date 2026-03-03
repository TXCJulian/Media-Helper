import { useEffect, useRef } from 'react'

interface LogPanelProps {
  log: string[]
  error: string
  hasStarted: boolean
  color?: 'blue' | 'indigo' | 'rose'
  idleMessage?: string
}

const iconColorClass = {
  blue: 'text-[var(--accent)]',
  indigo: 'text-[var(--accent-2)]',
  rose: 'text-[var(--accent-3)]',
}

const activeTextClass = {
  blue: 'text-[var(--accent-light)]',
  indigo: 'text-[var(--accent-2)]',
  rose: 'text-[var(--accent-3)]',
}

export default function LogPanel({
  log,
  error,
  hasStarted,
  color = 'blue',
  idleMessage = 'Ready...',
}: LogPanelProps) {
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [log, error])

  const text = log.length > 0 ? log.join('\n') : hasStarted ? '' : idleMessage
  const displayText = error ? `Error: ${error}` : text
  const isIdle = !error && log.length === 0 && !hasStarted

  return (
    <div className="glass mt-6 overflow-hidden">
      <div className="flex items-center gap-[0.6rem] border-b border-[var(--border)] px-5 py-[0.65rem]">
        <span className={`text-[0.75rem] ${iconColorClass[color]}`}>▸</span>
        <span className="text-[0.68rem] font-semibold uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
          Output
        </span>
      </div>
      <div ref={bodyRef} className="max-h-[180px] overflow-y-auto bg-[rgba(0,0,0,0.25)] px-5 py-4">
        <pre
          className={`font-[JetBrains_Mono,monospace] text-[0.78rem] leading-[1.7] whitespace-pre-wrap break-all ${
            error
              ? 'text-[var(--error)]'
              : isIdle
                ? 'text-[var(--text-tertiary)]'
                : activeTextClass[color]
          }`}
        >
          {displayText}
          {isIdle && (
            <span className="ml-[2px] inline-block h-[1.1em] w-[7px] animate-[blink_1s_step-end_infinite] bg-[var(--text-tertiary)] align-text-bottom" />
          )}
        </pre>
      </div>
    </div>
  )
}
