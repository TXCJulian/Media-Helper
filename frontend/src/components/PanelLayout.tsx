import type { ReactNode } from 'react'

interface PanelLayoutProps {
  title: string
  onBack: () => void
  rightElement?: ReactNode
  maxWidth?: string
  children: ReactNode
}

export default function PanelLayout({ title, onBack, rightElement, maxWidth = '780px', children }: PanelLayoutProps) {
  return (
    <div className="relative z-1 mx-auto min-h-screen px-6 py-8" style={{ maxWidth }}>
      <div className="mb-7 flex items-center gap-4">
        <button
          type="button"
          onClick={onBack}
          className="flex h-[38px] w-[38px] cursor-pointer items-center justify-center rounded-[10px] border border-[var(--glass-border)] bg-[var(--bg-glass)] text-base text-[var(--text-secondary)] backdrop-blur-[16px] transition-all duration-200 hover:border-[var(--glass-border-hover)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
        >
          ←
        </button>
        <h2 className="text-[1.35rem] font-semibold tracking-[-0.02em]">{title}</h2>
        {rightElement}
      </div>

      <div className="glass-strong stagger animate-[panelSlideIn_0.45s_cubic-bezier(0.4,0,0.2,1)] p-8">
        {children}
      </div>
    </div>
  )
}
