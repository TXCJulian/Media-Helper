import type { ProbeResult } from '@/types'

export interface BrowserCompatibilityReport {
  containerIssue: string | null
  videoIssue: string | null
  audioIssues: string[]
  hasIssues: boolean
}

const BROWSER_CONTAINER_EXTENSIONS = new Set([
  '.mp4',
  '.m4a',
  '.m4v',
  '.mov',
  '.webm',
  '.ogg',
  '.mp3',
  '.wav',
  '.aac',
  '.flac',
])

const BROWSER_VIDEO_CODECS = new Set(['h264', 'avc1', 'vp8', 'vp9', 'av1', 'theora'])
// Keep this frontend advisory list intentionally permissive; backend preview
// transcoding policy may diverge (e.g. HEVC/theora handling by browser target).
const SUPPORTED_AUDIO_CODECS = new Set([
  'aac',
  'mp3',
  'opus',
  'vorbis',
  'flac',
  'pcm_s16le',
  'pcm_s24le',
  'pcm_s32le',
  'pcm_f32le',
])

function getLowerCaseExtension(filePath: string): string {
  const dot = filePath.lastIndexOf('.')
  if (dot < 0) return ''
  return filePath.slice(dot).toLowerCase()
}

function normalizeCodec(codec: string | null | undefined): string {
  return (codec ?? '').toLowerCase().trim()
}

export function getBrowserCompatibilityReport(
  filePath: string,
  probe: Pick<ProbeResult, 'video_codec' | 'audio_streams'>,
): BrowserCompatibilityReport {
  const extension = getLowerCaseExtension(filePath)

  const containerIssue =
    extension && !BROWSER_CONTAINER_EXTENSIONS.has(extension)
      ? `Video container ${extension}`
      : null

  const videoCodec = normalizeCodec(probe.video_codec)
  const videoIssue =
    videoCodec && !BROWSER_VIDEO_CODECS.has(videoCodec)
      ? `Video codec ${probe.video_codec?.toUpperCase() ?? 'UNKNOWN'}`
      : null

  const unsupportedAudioCodecs = new Set<string>()
  for (const stream of probe.audio_streams) {
    const codec = normalizeCodec(stream.codec)
    if (codec && !SUPPORTED_AUDIO_CODECS.has(codec)) {
      unsupportedAudioCodecs.add(codec.toUpperCase())
    }
  }

  const audioIssues = Array.from(unsupportedAudioCodecs)
  const hasIssues = Boolean(containerIssue || videoIssue || audioIssues.length > 0)

  return {
    containerIssue,
    videoIssue,
    audioIssues,
    hasIssues,
  }
}

export function getBrowserCompatibilityMessage(report: BrowserCompatibilityReport): string {
  const reasons: string[] = []
  if (report.containerIssue) reasons.push(report.containerIssue)
  if (report.videoIssue) reasons.push(report.videoIssue)
  if (report.audioIssues.length > 0) reasons.push(`Audio track(s) ${report.audioIssues.join(', ')}`)

  return `${reasons.join(' / ')} may not be natively supported by your browser. If playback has no video/audio and you need it for cutting, enable transcoding for this file here.`
}
