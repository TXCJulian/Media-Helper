import { useEffect, useRef, useState } from 'react'

interface Option {
  label: string
  value: string
}

interface StyledSelectProps {
  label: string
  options: Option[]
  value: string
  onChange: (value: string) => void
}

const FOCUS_CLASS =
  'border-[var(--accent-5)] shadow-[0_0_0_3px_var(--accent-5-glow),0_0_20px_rgba(245,158,11,0.08)]'

export default function StyledSelect({ label, options, value, onChange }: StyledSelectProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

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

  const selectedLabel = options.find((o) => o.value === value)?.label ?? value

  const handleSelect = (opt: Option) => {
    onChange(opt.value)
    setIsOpen(false)
    triggerRef.current?.focus()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (isOpen && focusedIndex >= 0 && options[focusedIndex]) {
          handleSelect(options[focusedIndex]!)
        } else {
          setIsOpen(!isOpen)
          if (!isOpen) {
            const idx = options.findIndex((o) => o.value === value)
            setFocusedIndex(idx >= 0 ? idx : 0)
          }
        }
        break
      case 'ArrowDown':
        e.preventDefault()
        if (!isOpen) {
          setIsOpen(true)
          const idx = options.findIndex((o) => o.value === value)
          setFocusedIndex(idx >= 0 ? idx : 0)
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
    <div>
      <label className="field-label">{label}</label>
      <div ref={containerRef} className="relative">
        <button
          ref={triggerRef}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          onClick={() => {
            setIsOpen(!isOpen)
            if (!isOpen) {
              const idx = options.findIndex((o) => o.value === value)
              setFocusedIndex(idx >= 0 ? idx : 0)
            }
          }}
          onKeyDown={handleKeyDown}
          className={`flex h-[42px] w-full cursor-pointer items-center rounded-[10px] border bg-[var(--bg-input)] pr-9 pl-[0.85rem] text-left font-[Geist,sans-serif] text-[0.875rem] outline-none transition-all duration-250 ${
            isOpen
              ? `bg-[var(--bg-input-focus)] ${FOCUS_CLASS}`
              : 'border-[var(--border)] hover:border-[var(--glass-border-hover)]'
          }`}
        >
          <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">
            {selectedLabel}
          </span>
          <svg
            className={`pointer-events-none absolute right-[0.85rem] h-3 w-3 text-[var(--text-secondary)] transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
            viewBox="0 0 16 16"
            fill="currentColor"
          >
            <path d="M8 11L3 6h10z" />
          </svg>
        </button>

        {isOpen && (
          <div
            ref={listRef}
            className="absolute z-50 mt-[6px] max-h-[240px] w-full overflow-y-auto rounded-[10px] border border-[var(--glass-border)] bg-[#141420] p-[4px] shadow-[0_8px_32px_rgba(0,0,0,0.5),0_0_0_1px_rgba(255,255,255,0.06)]"
            role="listbox"
          >
            {options.map((opt, i) => {
              const isSelected = opt.value === value
              const isFocused = i === focusedIndex
              return (
                <div
                  key={opt.value}
                  role="option"
                  aria-selected={isSelected}
                  onClick={() => handleSelect(opt)}
                  onMouseEnter={() => setFocusedIndex(i)}
                  className={`cursor-pointer rounded-lg px-[0.75rem] py-[0.5rem] text-[0.84rem] transition-colors duration-100 ${
                    isSelected ? 'font-medium text-[var(--accent-5)]' : 'text-[var(--text-primary)]'
                  } ${isFocused ? 'bg-[var(--bg-glass-hover)]' : ''}`}
                >
                  {opt.label}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
