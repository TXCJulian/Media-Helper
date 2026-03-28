import { useId } from 'react'

interface ToggleSwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  color?: 'blue' | 'indigo' | 'rose' | 'emerald' | 'amber'
  label: string
}

const colorClasses = {
  blue: 'bg-[var(--accent)] border-[var(--accent)] shadow-[0_0_14px_var(--accent-glow)]',
  indigo: 'bg-[var(--accent-2)] border-[var(--accent-2)] shadow-[0_0_14px_var(--accent-2-glow)]',
  rose: 'bg-[var(--accent-3)] border-[var(--accent-3)] shadow-[0_0_14px_var(--accent-3-glow)]',
  emerald: 'bg-[var(--accent-4)] border-[var(--accent-4)] shadow-[0_0_14px_var(--accent-4-glow)]',
  amber: 'bg-[var(--accent-5)] border-[var(--accent-5)] shadow-[0_0_14px_var(--accent-5-glow)]',
}

export default function ToggleSwitch({
  checked,
  onChange,
  disabled,
  color = 'blue',
  label,
}: ToggleSwitchProps) {
  const id = useId()
  return (
    <div className="flex items-center gap-3 py-[0.2rem]">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={id}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-10 shrink-0 cursor-pointer rounded-xl border transition-all duration-250 ${
          checked ? colorClasses[color] : 'border-[var(--border)] bg-[rgba(255,255,255,0.07)]'
        } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
      >
        <span
          className={`absolute top-[2px] left-[2px] h-[18px] w-[18px] rounded-full transition-all duration-250 ease-[cubic-bezier(0.4,0,0.2,1)] ${
            checked ? 'translate-x-4 bg-white' : 'bg-[rgba(255,255,255,0.55)]'
          }`}
        />
      </button>
      <span id={id} className="text-[0.825rem] text-[var(--text-secondary)]">
        {label}
      </span>
    </div>
  )
}
