import type { ReactNode } from 'react'

/**
 * The small uppercase status tag used on job cards.
 *
 * The shape (radius, padding, weight, letter size) is shared by the cutter and
 * the downloader so a job reads the same in both; the colour is passed in by
 * the caller, because status colour is per-feature and per-meaning.
 */
export default function StatusPill({
  className = '',
  title,
  children,
}: {
  /** Colour classes, e.g. `bg-emerald-400/15 text-emerald-300`. */
  className?: string
  title?: string
  children: ReactNode
}) {
  return (
    <span
      title={title}
      className={`shrink-0 rounded px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-[0.06em] ${className}`}
    >
      {children}
    </span>
  )
}
