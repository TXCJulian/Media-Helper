import { useEffect, useRef, useState } from 'react'
import type { AudioStreamInfo } from '@/types'

interface AudioTrackSelectProps {
  streams: AudioStreamInfo[]
  value: number
  onChange: (index: number) => void
  disabled?: boolean
}

function formatTrack(stream: AudioStreamInfo, i: number): string {
  let label = `Track ${i + 1}: ${stream.codec.toUpperCase()} ${stream.channels}ch`
  if (stream.language) label += ` (${stream.language})`
  if (stream.title) label += ` — ${stream.title}`
  return label
}

export default function AudioTrackSelect({
  streams,
  value,
  onChange,
  disabled,
}: AudioTrackSelectProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [isOpen])

  // Scroll focused item into view
  useEffect(() => {
    if (!isOpen || focusedIndex < 0 || !listRef.current) return
    const items = listRef.current.children
    if (items[focusedIndex]) {
      ;(items[focusedIndex] as HTMLElement).scrollIntoView({ block: 'nearest' })
    }
  }, [focusedIndex, isOpen])

  const handleSelect = (streamIndex: number) => {
    onChange(streamIndex)
    setIsOpen(false)
    triggerRef.current?.focus()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return
    switch (e.key) {
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (isOpen && focusedIndex >= 0 && streams[focusedIndex]) {
          handleSelect(streams[focusedIndex]!.index)
        } else {
          setIsOpen(!isOpen)
          if (!isOpen) {
            const idx = streams.findIndex((s) => s.index === value)
            setFocusedIndex(idx >= 0 ? idx : 0)
          }
        }
        break
      case 'ArrowDown':
        e.preventDefault()
        if (!isOpen) {
          setIsOpen(true)
          const idx = streams.findIndex((s) => s.index === value)
          setFocusedIndex(idx >= 0 ? idx : 0)
        } else {
          setFocusedIndex((prev) => Math.min(prev + 1, streams.length - 1))
        }
        break
      case 'ArrowUp':
        e.preventDefault()
        if (isOpen) {
          setFocusedIndex((prev) => Math.max(prev - 1, 0))
        }
        break
      case 'Escape':
        e.preventDefault()
        setIsOpen(false)
        triggerRef.current?.focus()
        break
      case 'Tab':
        setIsOpen(false)
        break
    }
  }

  const selectedStream = streams.find((s) => s.index === value)
  const selectedListIndex = streams.findIndex((s) => s.index === value)
  const displayValue = selectedStream
    ? formatTrack(selectedStream, selectedListIndex)
    : 'Select audio track...'

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        onClick={() => {
          if (!disabled) {
            setIsOpen(!isOpen)
            if (!isOpen) {
              const idx = streams.findIndex((s) => s.index === value)
              setFocusedIndex(idx >= 0 ? idx : 0)
            }
          }
        }}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        className={`flex h-[42px] w-full cursor-pointer items-center rounded-[10px] border bg-[var(--bg-input)] pr-9 pl-[0.85rem] text-left font-[Geist,sans-serif] text-[0.875rem] outline-none transition-all duration-250 ${
          disabled ? 'cursor-not-allowed opacity-50' : ''
        } ${
          isOpen
            ? 'bg-[var(--bg-input-focus)] border-[var(--accent-4)] shadow-[0_0_0_3px_var(--accent-4-glow),0_0_20px_rgba(52,211,153,0.08)]'
            : 'border-[var(--border)] hover:border-[var(--glass-border-hover)]'
        }`}
      >
        <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">
          {displayValue}
        </span>
        <svg
          className={`pointer-events-none absolute right-[0.85rem] h-3 w-3 text-[var(--text-secondary)] transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
          viewBox="0 0 16 16"
          fill="currentColor"
        >
          <path d="M8 11L3 6h10z" />
        </svg>
      </button>

      {isOpen && streams.length > 0 && (
        <div
          ref={listRef}
          className="absolute z-50 mt-[6px] max-h-[240px] w-full overflow-y-auto rounded-[10px] border border-[var(--glass-border)] bg-[#141420] p-[4px] shadow-[0_8px_32px_rgba(0,0,0,0.5),0_0_0_1px_rgba(255,255,255,0.06)]"
          role="listbox"
        >
          {streams.map((stream, i) => {
            const isSelected = stream.index === value
            const isFocused = i === focusedIndex
            return (
              <div
                key={stream.index}
                role="option"
                aria-selected={isSelected}
                onClick={() => handleSelect(stream.index)}
                onMouseEnter={() => setFocusedIndex(i)}
                className={`cursor-pointer truncate rounded-lg px-[0.75rem] py-[0.5rem] text-[0.84rem] transition-colors duration-100 ${
                  isSelected
                    ? 'text-[var(--accent-4)] font-medium'
                    : 'text-[var(--text-primary)]'
                } ${isFocused ? 'bg-[var(--bg-glass-hover)]' : ''}`}
              >
                {formatTrack(stream, i)}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
