import type { ReactNode } from 'react'

interface FormSectionProps {
  label: string
  children: ReactNode
}

export default function FormSection({ label, children }: FormSectionProps) {
  return (
    <div className="mb-6">
      <div className="mb-3 text-[0.625rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
        {label}
      </div>
      {children}
    </div>
  )
}
