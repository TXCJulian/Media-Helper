/**
 * Line icons shared by the cutter's and the downloader's job cards.
 *
 * Same 24-viewport stroke geometry everywhere, so "delete this job" looks
 * identical no matter which feature the job belongs to.
 */

interface IconProps {
  size?: number
}

function Svg({ size = 16, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export function TrashIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2" />
    </Svg>
  )
}

export function PencilIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
    </Svg>
  )
}

export function PlayIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <polygon points="6 4 20 12 6 20 6 4" />
    </Svg>
  )
}

export function StopIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <rect x="6" y="6" width="12" height="12" rx="1.5" />
    </Svg>
  )
}

export function RetryIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <polyline points="21 3 21 9 15 9" />
      <path d="M20.49 15a9 9 0 11-2.13-9.36L21 9" />
    </Svg>
  )
}

export function SaveIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" />
      <polyline points="17 21 17 13 7 13 7 21" />
      <polyline points="7 3 7 8 15 8" />
    </Svg>
  )
}
