import { useEffect, useRef, useState } from 'react'

interface DirectorySelectProps {
  directories: string[]
  value: string
  onChange: (value: string) => void
  onRefresh: () => void
  isLoading: boolean
  disabled?: boolean
  color?: 'blue' | 'indigo' | 'rose' | 'emerald'
}

const focusClasses = {
  blue: 'border-[var(--accent)] shadow-[0_0_0_3px_var(--accent-glow),0_0_20px_rgba(59,130,246,0.08)]',
  indigo:
    'border-[var(--accent-2)] shadow-[0_0_0_3px_var(--accent-2-glow),0_0_20px_rgba(168,85,247,0.08)]',
  rose: 'border-[var(--accent-3)] shadow-[0_0_0_3px_var(--accent-3-glow),0_0_20px_rgba(244,114,182,0.08)]',
  emerald:
    'border-[var(--accent-4)] shadow-[0_0_0_3px_var(--accent-4-glow),0_0_20px_rgba(52,211,153,0.08)]',
}

const selectedTextClasses = {
  blue: 'text-[var(--accent-light)]',
  indigo: 'text-[var(--accent-2)]',
  rose: 'text-[var(--accent-3)]',
  emerald: 'text-[var(--accent-4)]',
}

export default function DirectorySelect({
  directories,
  value,
  onChange,
  onRefresh,
  isLoading,
  disabled,
  color = 'blue',
}: DirectorySelectProps) {
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

  const handleSelect = (dir: string) => {
    onChange(dir)
    setIsOpen(false)
    triggerRef.current?.focus()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return

    switch (e.key) {
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (isOpen && focusedIndex >= 0 && directories[focusedIndex]) {
          handleSelect(directories[focusedIndex]!)
        } else {
          setIsOpen(!isOpen)
          if (!isOpen) {
            const idx = directories.indexOf(value)
            setFocusedIndex(idx >= 0 ? idx : 0)
          }
        }
        break
      case 'ArrowDown':
        e.preventDefault()
        if (!isOpen) {
          setIsOpen(true)
          const idx = directories.indexOf(value)
          setFocusedIndex(idx >= 0 ? idx : 0)
        } else {
          setFocusedIndex((prev) => Math.min(prev + 1, directories.length - 1))
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

  const displayValue =
    value || (directories.length > 0 ? 'Select directory...' : 'No directories found')

  return (
    <div className="flex items-end gap-2">
      <div className="mb-0 flex-1">
        <label className="mb-[0.35rem] block text-[0.78rem] font-medium tracking-[0.005em] text-[var(--text-secondary)]">
          Directory
        </label>
        <div ref={containerRef} className="relative">
          {/* Trigger button */}
          <button
            ref={triggerRef}
            type="button"
            aria-haspopup="listbox"
            aria-expanded={isOpen}
            onClick={() => {
              if (!disabled) {
                setIsOpen(!isOpen)
                if (!isOpen) {
                  const idx = directories.indexOf(value)
                  setFocusedIndex(idx >= 0 ? idx : 0)
                }
              }
            }}
            onKeyDown={handleKeyDown}
            disabled={disabled || isLoading}
            className={`flex h-[42px] w-full cursor-pointer items-center rounded-[10px] border bg-[var(--bg-input)] pr-9 pl-[0.85rem] text-left font-[Geist,sans-serif] text-[0.875rem] outline-none transition-all duration-250 ${
              disabled || isLoading ? 'cursor-not-allowed opacity-50' : ''
            } ${
              isOpen
                ? `bg-[var(--bg-input-focus)] ${focusClasses[color]}`
                : 'border-[var(--border)] hover:border-[var(--glass-border-hover)]'
            }`}
          >
            <span
              className={`min-w-0 flex-1 truncate ${value ? 'text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]'}`}
            >
              {displayValue}
            </span>
            {/* Caret */}
            <svg
              className={`pointer-events-none absolute right-[0.85rem] h-3 w-3 text-[var(--text-secondary)] transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
              viewBox="0 0 16 16"
              fill="currentColor"
            >
              <path d="M8 11L3 6h10z" />
            </svg>
          </button>

          {/* Dropdown panel */}
          {isOpen && directories.length > 0 && (
            <div
              ref={listRef}
              className="absolute z-50 mt-[6px] max-h-[240px] w-full overflow-y-auto rounded-[10px] border border-[var(--glass-border)] bg-[#141420] p-[4px] shadow-[0_8px_32px_rgba(0,0,0,0.5),0_0_0_1px_rgba(255,255,255,0.06)]"
              role="listbox"
            >
              {directories.map((dir, i) => {
                const isSelected = dir === value
                const isFocused = i === focusedIndex
                return (
                  <div
                    key={dir}
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => handleSelect(dir)}
                    onMouseEnter={() => setFocusedIndex(i)}
                    className={`cursor-pointer truncate rounded-lg px-[0.75rem] py-[0.5rem] text-[0.84rem] transition-colors duration-100 ${
                      isSelected
                        ? `${selectedTextClasses[color]} font-medium`
                        : 'text-[var(--text-primary)]'
                    } ${isFocused ? 'bg-[var(--bg-glass-hover)]' : ''}`}
                  >
                    {dir}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={onRefresh}
        disabled={disabled || isLoading}
        title="Refresh"
        className="flex h-[42px] w-[42px] shrink-0 cursor-pointer items-center justify-center rounded-[10px] border border-[var(--border)] bg-[var(--bg-input)] text-base text-[var(--text-secondary)] transition-all duration-200 hover:border-[var(--glass-border-hover)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isLoading ? <span className="spinner-sm" /> : '↻'}
      </button>
    </div>
  )
}
