import { useEffect, useRef, useState } from 'react'

interface TrackModeOption {
  label: string
  value: string
}

interface TrackModeSelectProps {
  options: TrackModeOption[]
  value: string
  onChange: (value: string) => void
  disabled?: boolean
}

export default function TrackModeSelect({
  options,
  value,
  onChange,
  disabled = false,
}: TrackModeSelectProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

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

  useEffect(() => {
    if (!isOpen || focusedIndex < 0 || !listRef.current) return
    const items = listRef.current.children
    if (items[focusedIndex]) {
      ;(items[focusedIndex] as HTMLElement).scrollIntoView({ block: 'nearest' })
    }
  }, [focusedIndex, isOpen])

  const selectedOption = options.find((opt) => opt.value === value) ?? options[0]

  const openAndFocusSelected = () => {
    const selectedIndex = options.findIndex((opt) => opt.value === value)
    setFocusedIndex(selectedIndex >= 0 ? selectedIndex : 0)
    setIsOpen(true)
  }

  const handleSelect = (nextValue: string) => {
    onChange(nextValue)
    setIsOpen(false)
    triggerRef.current?.focus()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return

    switch (e.key) {
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (isOpen && focusedIndex >= 0 && options[focusedIndex]) {
          handleSelect(options[focusedIndex]!.value)
        } else {
          openAndFocusSelected()
        }
        break
      case 'ArrowDown':
        e.preventDefault()
        if (!isOpen) {
          openAndFocusSelected()
        } else {
          setFocusedIndex((prev) => Math.min(prev + 1, options.length - 1))
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

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        onClick={() => {
          if (disabled) return
          if (isOpen) {
            setIsOpen(false)
          } else {
            openAndFocusSelected()
          }
        }}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        className={`flex h-[32px] min-w-[112px] items-center rounded-[8px] border bg-[var(--bg-input)] pr-8 pl-2.5 text-left text-[0.72rem] outline-none transition-all duration-200 ${
          disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'
        } ${
          isOpen
            ? 'border-[var(--accent-4)] bg-[var(--bg-input-focus)] shadow-[0_0_0_2px_var(--accent-4-glow),0_0_16px_rgba(52,211,153,0.08)]'
            : 'border-[var(--border)] hover:border-[var(--glass-border-hover)]'
        }`}
      >
        <span className="truncate text-[var(--text-primary)]">
          {selectedOption?.label ?? value}
        </span>
        <svg
          className={`pointer-events-none absolute right-2.5 h-3 w-3 text-[var(--text-secondary)] transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
          viewBox="0 0 16 16"
          fill="currentColor"
        >
          <path d="M8 11L3 6h10z" />
        </svg>
      </button>

      {isOpen && options.length > 0 && (
        <div
          ref={listRef}
          className="absolute right-0 z-50 mt-[6px] max-h-[220px] min-w-[140px] overflow-y-auto rounded-[10px] border border-[var(--glass-border)] bg-[#141420] p-[4px] shadow-[0_8px_32px_rgba(0,0,0,0.5),0_0_0_1px_rgba(255,255,255,0.06)]"
          role="listbox"
        >
          {options.map((opt, i) => {
            const isSelected = opt.value === value
            const isFocused = i === focusedIndex
            return (
              <button
                key={opt.value}
                type="button"
                role="option"
                aria-selected={isSelected}
                onClick={() => handleSelect(opt.value)}
                onMouseEnter={() => setFocusedIndex(i)}
                className={`w-full cursor-pointer truncate rounded-lg px-[0.7rem] py-[0.46rem] text-left text-[0.78rem] transition-colors duration-100 ${
                  isSelected ? 'font-medium text-[var(--accent-4)]' : 'text-[var(--text-primary)]'
                } ${isFocused ? 'bg-[var(--bg-glass-hover)]' : 'bg-transparent'}`}
              >
                {opt.label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
