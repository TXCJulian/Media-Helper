import type { ReactNode } from 'react'

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
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={onChange}
      disabled={disabled}
      className={`encoder-select input-field input-teal cursor-pointer bg-[var(--bg-input)] text-[var(--text-primary)] focus:border-teal-400 focus:ring-2 focus:ring-teal-400/25 disabled:cursor-not-allowed disabled:opacity-50 ${className ?? ''}`}
    >
      {options.map((option) => {
        const normalized = typeof option === 'string' ? { value: option, label: option } : option
        return (
          <option
            key={normalized.value}
            value={normalized.value}
            disabled={normalized.disabled}
            className="bg-[#141420] text-[var(--text-primary)]"
          >
            {normalized.label ?? normalized.value}
          </option>
        )
      })}
    </select>
  )
}
