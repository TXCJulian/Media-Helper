import DirectorySelect from '../ui/DirectorySelect'
import FormSection from '../ui/FormSection'
import SegmentedControl from '../ui/SegmentedControl'
import StyledSelect from '../ui/StyledSelect'
import type { DirectoryEntry, DownloadForm } from '@/types'

const CONTAINERS: Record<string, { label: string; value: string }[]> = {
  video: [
    { label: 'Auto', value: 'auto' },
    { label: 'MP4', value: 'mp4' },
    { label: 'MKV', value: 'mkv' },
    { label: 'WebM', value: 'webm' },
    { label: 'MOV', value: 'mov' },
  ],
  audio: [
    { label: 'Auto', value: 'auto' },
    { label: 'MP3', value: 'mp3' },
    { label: 'M4A', value: 'm4a' },
    { label: 'FLAC', value: 'flac' },
    { label: 'Opus', value: 'opus' },
    { label: 'WAV', value: 'wav' },
  ],
  thumbnail: [
    { label: 'Auto', value: 'auto' },
    { label: 'JPG', value: 'jpg' },
    { label: 'PNG', value: 'png' },
    { label: 'WebP', value: 'webp' },
  ],
}

const RECODE: Record<string, { label: string; value: string }[]> = {
  video: [
    { label: 'No re-encode', value: 'auto' },
    { label: 'H.264', value: 'h264' },
    { label: 'H.265', value: 'h265' },
    { label: 'VP9', value: 'vp9' },
    { label: 'AV1', value: 'av1' },
  ],
  audio: [
    { label: 'No re-encode', value: 'auto' },
    { label: 'MP3', value: 'mp3' },
    { label: 'FLAC', value: 'flac' },
    { label: 'AAC', value: 'aac' },
    { label: 'Opus', value: 'opus' },
  ],
  thumbnail: [],
}

const VIDEO_QUALITY = [
  { label: 'Best', value: 'best' },
  { label: '2160p', value: '2160p' },
  { label: '1440p', value: '1440p' },
  { label: '1080p', value: '1080p' },
  { label: '720p', value: '720p' },
  { label: '480p', value: '480p' },
  { label: 'Worst', value: 'worst' },
]

const AUDIO_QUALITY = [
  { label: 'Best', value: 'best' },
  { label: '320kbps', value: '320kbps' },
  { label: '256kbps', value: '256kbps' },
  { label: '192kbps', value: '192kbps' },
  { label: '128kbps', value: '128kbps' },
  { label: '96kbps', value: '96kbps' },
  { label: 'Worst', value: 'worst' },
]

interface Props {
  form: DownloadForm
  onChange: (patch: Partial<DownloadForm>) => void
  directories: DirectoryEntry[]
  onRefreshDirectories: () => void
  isRefreshingDirectories: boolean
  showBaseLabel?: boolean
}

export default function DownloadOptions({
  form,
  onChange,
  directories,
  onRefreshDirectories,
  isRefreshingDirectories,
  showBaseLabel,
}: Props) {
  const isThumbnail = form.type === 'thumbnail'
  const quality = form.type === 'audio' ? AUDIO_QUALITY : VIDEO_QUALITY

  return (
    <>
      <FormSection label="Media">
        <div className="space-y-4">
          <SegmentedControl
            color="cyan"
            options={[
              { label: 'Video', value: 'video' },
              { label: 'Audio', value: 'audio' },
              { label: 'Thumbnail', value: 'thumbnail' },
            ]}
            value={form.type}
            onChange={(v) =>
              onChange({
                type: v as DownloadForm['type'],
                codec: 'auto',
                format: 'auto',
                quality: 'best',
              })
            }
          />

          <div className={`grid gap-3 ${isThumbnail ? 'sm:grid-cols-1' : 'sm:grid-cols-2'}`}>
            <StyledSelect
              color="cyan"
              label="Format"
              options={CONTAINERS[form.type] ?? []}
              value={form.format}
              onChange={(v) => onChange({ format: v })}
            />
            {!isThumbnail && (
              <StyledSelect
                color="cyan"
                label="Quality"
                options={quality}
                value={form.quality}
                onChange={(v) => onChange({ quality: v })}
              />
            )}
          </div>
        </div>
      </FormSection>

      <FormSection label="Destination">
        <div className="space-y-4">
          <DirectorySelect
            color="cyan"
            directories={directories}
            onRefresh={onRefreshDirectories}
            isLoading={isRefreshingDirectories}
            value={form.output_dir}
            base={form.base}
            onChange={(path, base) => onChange({ output_dir: path, base })}
            showBaseLabel={showBaseLabel}
          />

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="field-label">Subfolder</label>
              <input
                type="text"
                value={form.sub_folder}
                placeholder="e.g. music/albums"
                onChange={(e) => onChange({ sub_folder: e.target.value })}
                className="input-field input-cyan"
              />
            </div>
            <div>
              <label className="field-label">Playlist item limit</label>
              <input
                type="number"
                min="0"
                value={form.item_limit}
                placeholder="0 for no limit"
                onChange={(e) => onChange({ item_limit: Number(e.target.value) || 0 })}
                className="input-field input-cyan"
              />
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="field-label">Filename prefix</label>
              <input
                type="text"
                value={form.custom_prefix}
                placeholder="Added before the title"
                onChange={(e) => onChange({ custom_prefix: e.target.value })}
                className="input-field input-cyan"
              />
            </div>
            <div>
              <label className="field-label">Filename</label>
              <input
                type="text"
                value={form.custom_filename}
                placeholder="Defaults to the title"
                onChange={(e) => onChange({ custom_filename: e.target.value })}
                className="input-field input-cyan"
              />
            </div>
          </div>
        </div>
      </FormSection>

      <FormSection label="Queue">
        <div className="space-y-4">
          <SegmentedControl
            color="cyan"
            options={[
              { label: 'Start now', value: 'yes' },
              { label: 'Hold in queue', value: 'no' },
            ]}
            value={form.auto_start ? 'yes' : 'no'}
            onChange={(v) => onChange({ auto_start: v === 'yes' })}
          />

          {!isThumbnail && (
            <div className="sm:max-w-[50%]">
              <StyledSelect
                color="cyan"
                label="Re-encode to codec"
                options={RECODE[form.type] ?? []}
                value={form.codec}
                onChange={(v) => onChange({ codec: v })}
              />
              {form.codec !== 'auto' && (
                <p className="mt-2 text-[0.72rem] leading-relaxed text-[var(--text-tertiary)]">
                  Re-encoding runs after the download and usually takes longer than the download
                  itself. Leave this on “No re-encode” unless you need a specific codec.
                </p>
              )}
            </div>
          )}
        </div>
      </FormSection>
    </>
  )
}
