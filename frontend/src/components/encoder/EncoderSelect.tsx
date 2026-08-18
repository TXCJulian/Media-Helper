import { type ReactNode, useEffect, useRef, useState } from 'react'

export type EncoderSelectOption =
  | string
  | {
      value: string
      label?: ReactNode
      disabled?: boolean
    }

type EncoderSelectProps = {
  value: string
  options: EncoderSelectOption[]
  onChange: (event: React.ChangeEvent<HTMLSelectElement>) => void
  'aria-label': string
  disabled?: boolean
  className?: string
}

export default function EncoderSelect({
  value,
  options,
  onChange,
  'aria-label': ariaLabel,
  disabled,
  className,
}: EncoderSelectProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  const normalizedOptions = options.map((option) =>
    typeof option === 'string' ? { value: option, label: option, disabled: false } : option,
  )

  const selectedOption = normalizedOptions.find((option) => option.value === value)
  const displayLabel = selectedOption?.label ?? selectedOption?.value ?? value ?? 'Select option…'

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
      ;(items[focusedIndex] as HTMLElement).scrollIntoView?.({ block: 'nearest' })
    }
  }, [focusedIndex, isOpen])

  const findIndex = () => normalizedOptions.findIndex((option) => option.value === value)

  const handleSelect = (optionValue: string) => {
    const syntheticEvent = {
      target: { value: optionValue },
      currentTarget: { value: optionValue },
    } as unknown as React.ChangeEvent<HTMLSelectElement>
    onChange(syntheticEvent)
    setIsOpen(false)
    triggerRef.current?.focus()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return

    switch (e.key) {
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (isOpen && focusedIndex >= 0 && normalizedOptions[focusedIndex]) {
          const opt = normalizedOptions[focusedIndex]!
          if (!opt.disabled) handleSelect(opt.value)
        } else {
          setIsOpen(!isOpen)
          if (!isOpen) {
            const idx = findIndex()
            setFocusedIndex(idx >= 0 ? idx : 0)
          }
        }
        break
      case 'ArrowDown':
        e.preventDefault()
        if (!isOpen) {
          setIsOpen(true)
          const idx = findIndex()
          setFocusedIndex(idx >= 0 ? idx : 0)
        } else {
          setFocusedIndex((prev) => Math.min(prev + 1, normalizedOptions.length - 1))
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
    <div ref={containerRef} className={`relative ${className ?? ''}`}>
      {/* Hidden select for full HTMLSelectElement API and test compatibility */}
      <select
        aria-label={ariaLabel}
        value={value}
        onChange={onChange}
        disabled={disabled}
        className="encoder-select sr-only pointer-events-none"
        tabIndex={-1}
      >
        {normalizedOptions.map((opt) => (
          <option key={opt.value} value={opt.value} disabled={opt.disabled}>
            {typeof opt.label === 'string' ? opt.label : opt.value}
          </option>
        ))}
      </select>

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
              const idx = findIndex()
              setFocusedIndex(idx >= 0 ? idx : 0)
            }
          }
        }}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        className={`flex h-[42px] w-full cursor-pointer items-center justify-between rounded-[10px] border bg-[var(--bg-input)] pr-9 pl-[0.85rem] text-left font-[Geist,sans-serif] text-[0.875rem] outline-none transition-all duration-250 ${
          disabled ? 'cursor-not-allowed opacity-50' : ''
        } ${
          isOpen
            ? 'border-[var(--accent-5)] bg-[var(--bg-input-focus)] shadow-[0_0_0_3px_var(--accent-5-glow),0_0_20px_rgba(45,212,191,0.08)]'
            : 'border-[var(--border)] hover:border-[var(--glass-border-hover)]'
        }`}
      >
        <span
          className={`min-w-0 flex-1 truncate ${value ? 'text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]'}`}
        >
          {displayLabel}
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
      {isOpen && normalizedOptions.length > 0 && (
        <div
          ref={listRef}
          className="absolute z-50 mt-[6px] max-h-[240px] w-full overflow-y-auto rounded-[10px] border border-[var(--glass-border)] bg-[#141420] p-[4px] shadow-[0_8px_32px_rgba(0,0,0,0.5),0_0_0_1px_rgba(255,255,255,0.06)]"
          role="listbox"
        >
          {normalizedOptions.map((option, i) => {
            const isSelected = option.value === value
            const isFocused = i === focusedIndex
            return (
              <div
                key={option.value}
                role="option"
                aria-selected={isSelected}
                aria-disabled={option.disabled}
                onClick={() => {
                  if (!option.disabled) handleSelect(option.value)
                }}
                onMouseEnter={() => setFocusedIndex(i)}
                className={`flex cursor-pointer items-center justify-between rounded-lg px-[0.75rem] py-[0.5rem] text-[0.84rem] transition-colors duration-100 ${
                  option.disabled ? 'cursor-not-allowed opacity-40' : ''
                } ${
                  isSelected
                    ? 'font-medium text-[var(--accent-5)]'
                    : 'text-[var(--text-primary)]'
                } ${isFocused ? 'bg-[var(--bg-glass-hover)]' : ''}`}
              >
                <span className="truncate">{option.label ?? option.value}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
