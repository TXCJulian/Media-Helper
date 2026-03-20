export type PanelName = 'episodes' | 'music' | 'lyrics' | 'cutter'

interface LandingProps {
  onNavigate: (panel: PanelName) => void
  enabledFeatures: PanelName[]
  backendStatus: 'checking' | 'connected' | 'unreachable'
}

const cards: {
  id: PanelName
  icon: string
  title: string
  desc: string
  colorClass: string
  iconClass: string
}[] = [
  {
    id: 'episodes',
    icon: '▶',
    title: 'Episode Renamer',
    desc: 'Rename TV show episodes using TMDB metadata.',
    colorClass: 'card-episodes',
    iconClass: 'bg-[var(--accent-glow)] text-[var(--accent-light)]',
  },
  {
    id: 'music',
    icon: '♫',
    title: 'Music Renamer',
    desc: 'Rename music files based on metadata tags.',
    colorClass: 'card-music',
    iconClass: 'bg-[var(--accent-2-glow)] text-[var(--accent-2)]',
  },
  {
    id: 'lyrics',
    icon: '¶',
    title: 'Lyrics Transcriber',
    desc: 'Transcribe lyrics from audio files using whisper.',
    colorClass: 'card-lyrics',
    iconClass: 'bg-[var(--accent-3-glow)] text-[var(--accent-3)]',
  },
  {
    id: 'cutter',
    icon: '✂',
    title: 'Media Cutter',
    desc: 'Trim audio and video files with ffmpeg.',
    colorClass: 'card-cutter',
    iconClass: 'bg-[var(--accent-4-glow)] text-[var(--accent-4)]',
  },
]

export default function Landing({ onNavigate, enabledFeatures, backendStatus }: LandingProps) {
  const visibleCards = cards
    .filter((card) => enabledFeatures.includes(card.id))
    .sort((a, b) => enabledFeatures.indexOf(a.id) - enabledFeatures.indexOf(b.id))

  return (
    <div className="relative z-1 flex min-h-screen flex-col items-center justify-center p-8">
      <h1
        className="mb-[0.35rem] bg-gradient-to-br from-white to-[var(--text-secondary)] bg-clip-text text-[2.5rem] font-bold tracking-[-0.03em]"
        style={{ WebkitTextFillColor: 'transparent' }}
      >
        Media Helper
      </h1>
      <p className="mb-12 text-[0.9rem] font-normal tracking-[0.01em] text-[var(--text-tertiary)]">
        Organize your media library
      </p>

      {backendStatus === 'unreachable' ? (
        <div className="flex max-w-[400px] flex-col items-center justify-center rounded-2xl border border-red-500/30 bg-red-500/5 p-8 text-center">
          <span className="mb-3 text-[2rem]">⚠</span>
          <p className="mb-2 font-semibold text-[var(--text-secondary)]">Backend Unreachable</p>
          <p className="text-[0.9rem] text-[var(--text-tertiary)]">
            Unable to connect to the backend service. Please check that the server is running and
            accessible.
          </p>
        </div>
      ) : visibleCards.length > 0 ? (
        <div
          className={`grid w-full max-w-[1100px] grid-cols-1 gap-5 ${
            visibleCards.length >= 4
              ? 'md:grid-cols-2 lg:grid-cols-4'
              : visibleCards.length === 3
                ? 'md:grid-cols-3'
                : visibleCards.length === 2
                  ? 'md:grid-cols-2'
                  : ''
          }`}
        >
          {visibleCards.map((card, i) => (
            <button
              key={card.id}
              type="button"
              onClick={() => onNavigate(card.id)}
              className={`glass ${card.colorClass} group relative flex min-h-[220px] cursor-pointer flex-col overflow-hidden p-8 text-left transition-all duration-400 ease-[cubic-bezier(0.4,0,0.2,1)] hover:-translate-y-[5px] hover:border-[var(--glass-border-hover)] hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.1),0_8px_32px_rgba(0,0,0,0.3)]`}
              style={{ animationDelay: `${(i + 1) * 0.1}s`, animation: 'cardIn 0.5s ease both' }}
            >
              {/* Hover glow overlay */}
              <span
                className="pointer-events-none absolute inset-0 rounded-2xl opacity-0 transition-opacity duration-400 group-hover:opacity-100"
                style={{
                  background:
                    card.id === 'episodes'
                      ? 'radial-gradient(ellipse at 30% 80%, var(--accent-glow) 0%, transparent 65%)'
                      : card.id === 'music'
                        ? 'radial-gradient(ellipse at 30% 80%, var(--accent-2-glow) 0%, transparent 65%)'
                        : card.id === 'cutter'
                          ? 'radial-gradient(ellipse at 30% 80%, var(--accent-4-glow) 0%, transparent 65%)'
                          : 'radial-gradient(ellipse at 30% 80%, var(--accent-3-glow) 0%, transparent 65%)',
                }}
              />

              <span
                className={`relative z-1 mb-5 flex h-[46px] w-[46px] items-center justify-center rounded-xl text-[1.2rem] ${card.iconClass}`}
              >
                {card.icon}
              </span>
              <span className="relative z-1 mb-[0.45rem] text-[1.05rem] font-semibold tracking-[-0.01em]">
                {card.title}
              </span>
              <span className="relative z-1 text-[0.8rem] leading-[1.55] text-[var(--text-tertiary)]">
                {card.desc}
              </span>
              <span className="relative z-1 mt-auto pt-4 text-[0.8rem] text-[var(--text-tertiary)] transition-all duration-250 group-hover:translate-x-[5px] group-hover:text-[var(--text-secondary)]">
                →
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
