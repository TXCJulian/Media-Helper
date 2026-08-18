function compactJson(value: object): string {
  try {
    return JSON.stringify(value) ?? '[unserializable value]'
  } catch {
    return '[unserializable value]'
  }
}

function formatValue(value: unknown): string {
  return typeof value === 'object' && value !== null ? compactJson(value) : String(value)
}

export function formatBytes(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return formatValue(value)
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let bytes = value
  let unit = 0
  while (Math.abs(bytes) >= 1024 && unit < units.length - 1) {
    bytes /= 1024
    unit += 1
  }
  return `${unit === 0 ? bytes.toFixed(0) : bytes.toFixed(1)} ${units[unit]}`
}

export function formatDuration(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return formatValue(value)
  const seconds = Math.round(value)
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainder = seconds % 60
  if (hours > 0)
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
  return `${minutes}:${String(remainder).padStart(2, '0')}`
}

export function formatFrameRate(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return formatValue(value)
  return `${Number(value.toFixed(3))} fps`
}

function audioChannelLabel(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return ''
  if (value === 1) return 'mono'
  if (value === 2) return 'stereo'
  if (value === 6) return '5.1'
  if (value === 8) return '7.1'
  return `${value}ch`
}

export function formatAudioTracks(value: unknown): string {
  if (!Array.isArray(value))
    return typeof value === 'object' && value !== null ? compactJson(value) : ''
  return value
    .map((track) => {
      if (typeof track !== 'object' || track === null || Array.isArray(track))
        return formatValue(track)
      const details = track as Record<string, unknown>
      const codec = typeof details.codec === 'string' ? details.codec : ''
      const channels = audioChannelLabel(details.channels)
      const language = typeof details.language === 'string' ? details.language : ''
      const title = typeof details.title === 'string' ? details.title : ''
      const label = [codec, channels, language, title].filter(Boolean).join(' · ')
      return label || compactJson(details)
    })
    .filter(Boolean)
    .join('\n')
}

export function formatFactValue(
  key: string,
  value: unknown,
  facts: Record<string, unknown>,
): string | null {
  if (value === null || value === undefined || value === '') return null
  switch (key) {
    case 'height':
      return `${formatValue(value)}p`
    case 'width':
      return facts.height == null ? `${formatValue(value)}px wide` : null
    case 'hdr':
      return typeof value === 'boolean' ? (value ? 'HDR' : 'SDR') : formatValue(value)
    case 'dolby_vision':
      return typeof value === 'boolean' ? (value ? 'Dolby Vision' : null) : formatValue(value)
    case 'bit_depth':
      return `${formatValue(value)}-bit`
    case 'duration':
      return formatDuration(value)
    case 'frame_rate':
      return formatFrameRate(value)
    case 'audio':
      return formatAudioTracks(value)
    case 'bit_rate':
      return typeof value === 'number'
        ? `${(value / 1_000_000).toFixed(1)} Mbps`
        : formatValue(value)
    case 'size':
      return formatBytes(value)
    case 'video_codec':
      return typeof value === 'string' ? value.toUpperCase() : formatValue(value)
    default: {
      const label = key.replace(/_/g, ' ')
      if (typeof value === 'boolean') return value ? label : null
      if (typeof value === 'object') return `${label}: ${compactJson(value)}`
      return `${label}: ${formatValue(value)}`
    }
  }
}
