import FormSection from '@/components/ui/FormSection'
import ToggleSwitch from '@/components/ui/ToggleSwitch'
import SegmentedControl from '@/components/ui/SegmentedControl'

interface OutputSettingsProps {
  outputName: string
  streamCopy: boolean
  codec: string
  audioCodec: string
  container: string
  isVideo: boolean
  onOutputNameChange: (name: string) => void
  onStreamCopyChange: (value: boolean) => void
  onCodecChange: (codec: string) => void
  onAudioCodecChange: (codec: string) => void
  onContainerChange: (container: string) => void
}

const audioCodecOptions = [
  { label: 'AAC', value: 'aac' },
  { label: 'AC3', value: 'ac3' },
  { label: 'FLAC', value: 'flac' },
  { label: 'Opus', value: 'opus' },
  { label: 'MP3', value: 'mp3' },
]

const videoAudioCodecOptions = [
  { label: 'AAC', value: 'aac' },
  { label: 'AC3', value: 'ac3' },
  { label: 'FLAC', value: 'flac' },
  { label: 'Opus', value: 'opus' },
]

const videoCodecOptions = [
  { label: 'H.264', value: 'libx264' },
  { label: 'H.265', value: 'libx265' },
  { label: 'VP9', value: 'libvpx-vp9' },
  { label: 'AV1', value: 'libaom-av1' },
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

export default function OutputSettings({
  outputName,
  streamCopy,
  codec,
  audioCodec,
  container,
  isVideo,
  onOutputNameChange,
  onStreamCopyChange,
  onCodecChange,
  onAudioCodecChange,
  onContainerChange,
}: OutputSettingsProps) {
  const containerOptions = isVideo ? videoContainerOptions : audioContainerOptions
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
          label={streamCopy ? 'Stream Copy (fast)' : 'Re-encode (precise)'}
        />
      </FormSection>

      {!streamCopy && (
        <>
          {isVideo ? (
            <>
              <FormSection label="Video Codec">
                <SegmentedControl
                  options={videoCodecOptions}
                  value={codec}
                  onChange={onCodecChange}
                  color="emerald"
                />
              </FormSection>
              <FormSection label="Audio Codec">
                <SegmentedControl
                  options={videoAudioCodecOptions}
                  value={audioCodec}
                  onChange={onAudioCodecChange}
                  color="emerald"
                />
              </FormSection>
            </>
          ) : (
            <FormSection label="Codec">
              <SegmentedControl
                options={audioCodecOptions}
                value={codec}
                onChange={onCodecChange}
                color="emerald"
              />
            </FormSection>
          )}

          <FormSection label="Container">
            <SegmentedControl
              options={containerOptions}
              value={container}
              onChange={onContainerChange}
              color="emerald"
            />
          </FormSection>
        </>
      )}
    </div>
  )
}
