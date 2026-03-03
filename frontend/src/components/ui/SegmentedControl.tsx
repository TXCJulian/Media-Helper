interface SegmentedControlProps {
  options: { label: string; value: string }[]
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  color?: 'blue' | 'indigo' | 'rose'
}

const activeClasses = {
  blue: 'bg-[var(--accent)] text-white shadow-[0_2px_10px_var(--accent-glow)]',
  indigo: 'bg-[var(--accent-2)] text-white shadow-[0_2px_10px_var(--accent-2-glow)]',
  rose: 'bg-[var(--accent-3)] text-white shadow-[0_2px_10px_var(--accent-3-glow)]',
}

export default function SegmentedControl({
  options,
  value,
  onChange,
  disabled,
  color = 'blue',
}: SegmentedControlProps) {
  return (
    <div
      className="inline-flex gap-[2px] rounded-[10px] border border-[var(--border)] bg-[var(--bg-input)] p-[3px]"
      role="radiogroup"
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          role="radio"
          aria-checked={value === opt.value}
          disabled={disabled}
          onClick={() => onChange(opt.value)}
          className={`cursor-pointer rounded-lg border-none px-4 py-[0.45rem] font-[Geist,sans-serif] text-[0.78rem] font-medium transition-all duration-250 ${
            value === opt.value
              ? activeClasses[color]
              : 'bg-transparent text-[var(--text-tertiary)] hover:bg-[rgba(255,255,255,0.025)] hover:text-[var(--text-secondary)]'
          } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
