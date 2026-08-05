import type { ReactNode } from 'react'

/**
 * A compact icon-only control for a job's secondary and destructive actions.
 *
 * Lifted out of the cutter's job list so the downloader can use the same
 * idiom: a dim glyph that lights up in the relevant colour on hover, rather
 * than a wordy bordered button. Icon-only means the label has to live
 * somewhere, so `label` is required and becomes both the accessible name and
 * the tooltip.
 */
export default function IconButton({
  label,
  onClick,
  disabled = false,
  tone = 'neutral',
  accentClass = '',
  children,
}: {
  /** Accessible name and tooltip. Required — there is no visible text. */
  label: string
  onClick: () => void
  disabled?: boolean
  /** `danger` lights up red on hover; `accent` uses `accentClass`. */
  tone?: 'neutral' | 'danger' | 'accent'
  /** Hover classes for `tone="accent"`, e.g. `hover:text-emerald-400`. */
  accentClass?: string
  children: ReactNode
}) {
  const hover =
    tone === 'danger'
      ? 'hover:bg-red-500/10 hover:text-red-400'
      : tone === 'accent'
        ? accentClass
        : 'hover:bg-white/8 hover:text-[var(--text-primary)]'
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`rounded-md p-1.5 transition ${
        disabled ? 'cursor-not-allowed text-white/10' : `text-white/25 ${hover}`
      }`}
    >
      {children}
    </button>
  )
}
