import { useEffect, useState } from 'react'
import FormSection from '@/components/ui/FormSection'
import ToggleSwitch from '@/components/ui/ToggleSwitch'
import SegmentedControl from '@/components/ui/SegmentedControl'
import TrackModeSelect from '@/components/cutter/TrackModeSelect'
import {
  isAudioCodecCompatible,
  isPassthruCompatible,
  audioCodecsForContainer,
  incompatibleVideoCodecs,
  incompatibleContainers,
  bestContainerForCodec,
  bestCodecForContainer,
  bestAudioCodecForContainer,
} from '@/lib/codecCompat'
import type { AudioTrackConfig, AudioStreamInfo } from '@/types'

interface OutputSettingsProps {
  outputName: string
  streamCopy: boolean
  codec: string
  container: string
  keepQuality: boolean
  audioTracks: AudioTrackConfig[]
  audioStreams: AudioStreamInfo[]
  isVideo: boolean
  sourceVideoBitrate: number | null
  onOutputNameChange: (name: string) => void
  onStreamCopyChange: (value: boolean) => void
  onCodecChange: (codec: string) => void
  onContainerChange: (container: string) => void
  onKeepQualityChange: (value: boolean) => void
  onAudioTracksChange: (tracks: AudioTrackConfig[]) => void
}

const audioCodecOptions = [
  { label: 'AAC', value: 'aac' },
  { label: 'AC3', value: 'ac3' },
  { label: 'DTS', value: 'dts' },
  { label: 'TrueHD', value: 'truehd' },
  { label: 'FLAC', value: 'flac' },
  { label: 'Opus', value: 'opus' },
  { label: 'Vorbis', value: 'vorbis' },
  { label: 'MP3', value: 'mp3' },
]

const videoCodecOptions = [
  { label: 'H.264', value: 'libx264' },
  { label: 'H.265', value: 'libx265' },
  { label: 'VP9', value: 'libvpx-vp9' },
  { label: 'AV1', value: 'libsvtav1' },
]

const audioContainerOptions = [
  { label: 'MP4', value: 'mp4' },
  { label: 'MKA', value: 'mka' },
  { label: 'FLAC', value: 'flac' },
  { label: 'OGG', value: 'ogg' },
  { label: 'MP3', value: 'mp3' },
]

const videoContainerOptions = [
  { label: 'MP4', value: 'mp4' },
  { label: 'MKV', value: 'mkv' },
  { label: 'WebM', value: 'webm' },
  { label: 'MOV', value: 'mov' },
]

const modeOptions = [
  { label: 'Passthru', value: 'passthru' },
  { label: 'Re-encode', value: 'reencode' },
  { label: 'Remove', value: 'remove' },
]

function formatBitrate(bps: number): string {
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(1)} Mbps`
  if (bps >= 1_000) return `${(bps / 1_000).toFixed(0)} kbps`
  return `${bps} bps`
}

function formatTrackLabel(stream: AudioStreamInfo, i: number): string {
  let label = `Track ${i + 1}: ${stream.codec.toUpperCase()} ${stream.channels}ch`
  if (stream.language) label += ` (${stream.language})`
  if (stream.title) label += ` - ${stream.title}`
  if (stream.bit_rate > 0) label += ` · ${formatBitrate(stream.bit_rate)}`
  return label
}

export default function OutputSettings({
  outputName,
  streamCopy,
  codec,
  container,
  keepQuality,
  audioTracks,
  audioStreams,
  isVideo,
  sourceVideoBitrate,
  onOutputNameChange,
  onStreamCopyChange,
  onCodecChange,
  onContainerChange,
  onKeepQualityChange,
  onAudioTracksChange,
}: OutputSettingsProps) {
  const containerOptions = isVideo ? videoContainerOptions : audioContainerOptions
  const [showAdvanced, setShowAdvanced] = useState(false)
  const isReencode = !streamCopy && isVideo

  // ── "Last touch wins" handlers ─────────────────────────────────
  // All options stay visible. When the user picks a value, the *other*
  // field auto-corrects if the combination is incompatible.

  const handleCodecChange = (newCodec: string) => {
    onCodecChange(newCodec)
    if (isReencode) {
      const fixed = bestContainerForCodec(newCodec, container, containerOptions)
      if (fixed !== container) onContainerChange(fixed)
    }
  }

  const handleContainerChange = (newContainer: string) => {
    onContainerChange(newContainer)
    if (isReencode) {
      const fixed = bestCodecForContainer(newContainer, codec, videoCodecOptions)
      if (fixed !== codec) onCodecChange(fixed)
    }
    // Auto-correct audio tracks when container changes:
    // - Passthru + source can't be stream-copied → re-encode with best codec
    // - Re-encode + source can now be stream-copied → switch back to passthru
    // - Re-encode + target codec incompatible → pick best compatible codec
    const corrected = audioTracks.map((t) => {
      const src = audioStreams.find((s) => s.index === t.streamIndex)
      const canPassthru = !src || isPassthruCompatible(src.codec, newContainer)

      if (t.mode === 'passthru' && !canPassthru) {
        return {
          ...t,
          mode: 'reencode' as const,
          codec: bestAudioCodecForContainer(newContainer, src!.codec, audioCodecOptions),
        }
      }
      if (t.mode === 'reencode' && canPassthru) {
        return { ...t, mode: 'passthru' as const }
      }
      if (t.mode === 'reencode' && !isAudioCodecCompatible(t.codec, newContainer)) {
        return {
          ...t,
          codec: bestAudioCodecForContainer(newContainer, t.codec, audioCodecOptions),
        }
      }
      return t
    })
    if (corrected.some((t, i) => t !== audioTracks[i])) {
      onAudioTracksChange(corrected)
    }
  }

  // Auto-correct tracks whose mode is no longer valid for the current container.
  // The container-change handler covers most cases, but initial render or
  // external state changes can leave a track in passthru when the source codec
  // can't be stream-copied into the target container.
  useEffect(() => {
    const corrected = audioTracks.map((t) => {
      if (t.mode !== 'passthru') return t
      const src = audioStreams.find((s) => s.index === t.streamIndex)
      if (!src || isPassthruCompatible(src.codec, container)) return t
      return {
        ...t,
        mode: 'reencode' as const,
        codec: bestAudioCodecForContainer(container, src.codec, audioCodecOptions),
      }
    })
    if (corrected.some((t, i) => t !== audioTracks[i])) {
      onAudioTracksChange(corrected)
    }
  }, [audioTracks, audioStreams, container, onAudioTracksChange])

  const filteredAudioCodecs = audioCodecsForContainer(audioCodecOptions, container)

  // Sets of values that conflict with the *other* field's current selection
  const incompatCodecs = isReencode
    ? incompatibleVideoCodecs(videoCodecOptions, container)
    : undefined
  const incompatContainers = isReencode
    ? incompatibleContainers(containerOptions, codec)
    : undefined

  const updateTrack = (streamIndex: number, updates: Partial<AudioTrackConfig>) => {
    onAudioTracksChange(
      audioTracks.map((track) =>
        track.streamIndex === streamIndex ? { ...track, ...updates } : track,
      ),
    )
  }

  const wouldRemoveAllAudio = (streamIndex: number) =>
    audioTracks.every((t) => t.streamIndex === streamIndex || t.mode === 'remove')

  const hasTrackReencode = audioTracks.some((track) => track.mode === 'reencode')
  const showAdvancedContent = !streamCopy || showAdvanced

  useEffect(() => {
    // Stream-copy mode starts collapsed, while re-encode mode stays expanded.
    if (!streamCopy) {
      setShowAdvanced(true)
    } else {
      setShowAdvanced(false)
    }
  }, [streamCopy])

  return (
    <div className="space-y-2">
      <FormSection label="Output filename">
        <input
          type="text"
          className="input-field input-emerald"
          value={outputName}
          onChange={(e) => onOutputNameChange(e.target.value)}
          placeholder="Same as original"
        />
      </FormSection>

      <FormSection label="Encoding">
        <ToggleSwitch
          checked={streamCopy}
          onChange={onStreamCopyChange}
          color="emerald"
          label={
            streamCopy
              ? hasTrackReencode
                ? 'Stream Copy (video)'
                : 'Stream Copy (fast, lossless)'
              : 'Re-encode (precise, lossy)'
          }
        />
      </FormSection>

      {!streamCopy && (
        <FormSection label="Match Source Quality">
          <ToggleSwitch
            checked={keepQuality}
            onChange={onKeepQualityChange}
            color="emerald"
            label={keepQuality ? 'On - matching source bitrate' : 'Off - encoder defaults'}
          />
          {keepQuality && sourceVideoBitrate != null && sourceVideoBitrate > 0 && isVideo && (
            <p className="mt-1 text-[0.68rem] text-white/35">
              Source video: {formatBitrate(sourceVideoBitrate)}
            </p>
          )}
          <p className="mt-1 text-[0.68rem] text-white/25">
            Re-encoding always causes some quality loss vs stream copy
          </p>
        </FormSection>
      )}

      {streamCopy && (
        <div className="mt-[0.85rem] mb-6">
          <button
            type="button"
            onClick={() => setShowAdvanced((prev) => !prev)}
            className="flex cursor-pointer items-center gap-2 border-none bg-none p-0 font-[Geist,sans-serif] text-[0.75rem] text-[var(--text-tertiary)] transition-colors duration-200 hover:text-[var(--text-secondary)]"
          >
            <span
              className={`text-[0.55rem] transition-transform duration-200 ${showAdvanced ? 'rotate-90' : ''}`}
            >
              ▶
            </span>
            Advanced Output Options
          </button>
        </div>
      )}

      {showAdvancedContent && (
        <div className="space-y-2">
          {!streamCopy && isVideo && (
            <FormSection label="Video Codec">
              <SegmentedControl
                options={videoCodecOptions}
                value={codec}
                onChange={handleCodecChange}
                incompatible={incompatCodecs}
                color="emerald"
              />
            </FormSection>
          )}

          <FormSection label="Container">
            <SegmentedControl
              options={containerOptions}
              value={container}
              onChange={handleContainerChange}
              incompatible={incompatContainers}
              color="emerald"
            />
          </FormSection>

          {audioStreams.length > 0 && (
            <FormSection label="Audio Tracks">
              <div className="space-y-2">
                {audioStreams.map((stream, i) => {
                  const track = audioTracks.find((t) => t.streamIndex === stream.index)
                  const mode = track?.mode ?? 'passthru'
                  const trackCodec = track?.codec ?? 'aac'

                  return (
                    <div
                      key={stream.index}
                      className={`rounded-lg border px-3 py-2 transition-colors ${
                        mode === 'remove'
                          ? 'border-red-500/25 bg-red-500/8'
                          : 'border-[var(--glass-border)] bg-[var(--glass-bg)]'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={`min-w-0 flex-1 truncate text-[0.75rem] ${
                            mode === 'remove' ? 'text-white/45' : 'text-white/70'
                          }`}
                        >
                          {formatTrackLabel(stream, i)}
                        </span>
                        <TrackModeSelect
                          options={modeOptions.filter((o) => {
                            if (
                              o.value === 'remove' &&
                              !isVideo &&
                              wouldRemoveAllAudio(stream.index)
                            )
                              return false
                            if (
                              o.value === 'passthru' &&
                              !isPassthruCompatible(stream.codec, container)
                            )
                              return false
                            return true
                          })}
                          value={mode}
                          onChange={(value) =>
                            updateTrack(stream.index, {
                              mode: value as AudioTrackConfig['mode'],
                            })
                          }
                        />
                      </div>
                      {mode === 'reencode' && (
                        <div className="mt-2">
                          <SegmentedControl
                            options={filteredAudioCodecs}
                            value={trackCodec}
                            onChange={(value) => updateTrack(stream.index, { codec: value })}
                            color="emerald"
                          />
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </FormSection>
          )}
        </div>
      )}
    </div>
  )
}
