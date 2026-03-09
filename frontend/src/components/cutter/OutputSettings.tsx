import FormSection from '@/components/ui/FormSection'
import ToggleSwitch from '@/components/ui/ToggleSwitch'
import SegmentedControl from '@/components/ui/SegmentedControl'

interface OutputSettingsProps {
  outputName: string
  streamCopy: boolean
  codec: string
  container: string
  onOutputNameChange: (name: string) => void
  onStreamCopyChange: (value: boolean) => void
  onCodecChange: (codec: string) => void
  onContainerChange: (container: string) => void
}

const codecOptions = [
  { label: 'AAC', value: 'aac' },
  { label: 'AC3', value: 'ac3' },
  { label: 'FLAC', value: 'flac' },
  { label: 'Opus', value: 'opus' },
  { label: 'MP3', value: 'mp3' },
]

const containerOptions = [
  { label: 'MP4', value: 'mp4' },
  { label: 'MKV', value: 'mkv' },
  { label: 'WebM', value: 'webm' },
  { label: 'FLAC', value: 'flac' },
  { label: 'OGG', value: 'ogg' },
]

export default function OutputSettings({
  outputName,
  streamCopy,
  codec,
  container,
  onOutputNameChange,
  onStreamCopyChange,
  onCodecChange,
  onContainerChange,
}: OutputSettingsProps) {
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
          <FormSection label="Codec">
            <SegmentedControl
              options={codecOptions}
              value={codec}
              onChange={onCodecChange}
              color="emerald"
            />
          </FormSection>

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
