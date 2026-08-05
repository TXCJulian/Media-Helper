import DirectorySelect from '../ui/DirectorySelect'
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
  advancedOpen: boolean
  onToggleAdvanced: () => void
}

export default function DownloadOptions({
  form,
  onChange,
  directories,
  onRefreshDirectories,
  isRefreshingDirectories,
  showBaseLabel,
  advancedOpen,
  onToggleAdvanced,
}: Props) {
  const isThumbnail = form.type === 'thumbnail'
  const quality = form.type === 'audio' ? AUDIO_QUALITY : VIDEO_QUALITY

  return (
    <>
      <div className={`grid gap-3 ${isThumbnail ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-3'}`}>
        <StyledSelect
          label="Type"
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
        <StyledSelect
          label="Format"
          options={CONTAINERS[form.type] ?? []}
          value={form.format}
          onChange={(v) => onChange({ format: v })}
        />
        {!isThumbnail && (
          <StyledSelect
            label="Quality"
            options={quality}
            value={form.quality}
            onChange={(v) => onChange({ quality: v })}
          />
        )}
      </div>

      <div className="rounded-[14px] border border-white/6 bg-white/[0.02]">
        <button
          type="button"
          onClick={onToggleAdvanced}
          className="flex w-full items-center justify-between px-5 py-3"
        >
          <span className="text-[0.78rem] font-medium uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
            Advanced Options
          </span>
          <span className="text-[0.75rem] text-[var(--text-tertiary)]">
            {advancedOpen ? '▴ collapse' : '▾ expand'}
          </span>
        </button>

        {advancedOpen && (
          <div className="border-t border-white/6 px-5 pb-5 pt-4">
            {!isThumbnail && (
              <div className="mb-5 rounded-[10px] border border-white/6 bg-white/[0.02] p-4">
                <StyledSelect
                  label="Re-encode to codec"
                  options={RECODE[form.type] ?? []}
                  value={form.codec}
                  onChange={(v) => onChange({ codec: v })}
                />
                {form.codec !== 'auto' && (
                  <p className="mt-2 text-[0.72rem] text-amber-400/80">
                    Re-encoding runs after the download and can take much longer than the download
                    itself. Leave this on “No re-encode” unless you need a specific codec.
                  </p>
                )}
              </div>
            )}

            <div className="grid gap-5 md:grid-cols-2">
              <StyledSelect
                label="Auto Start"
                options={[
                  { label: 'Yes', value: 'yes' },
                  { label: 'No', value: 'no' },
                ]}
                value={form.auto_start ? 'yes' : 'no'}
                onChange={(v) => onChange({ auto_start: v === 'yes' })}
              />

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
                <label className="field-label">Custom Name Prefix</label>
                <input
                  type="text"
                  value={form.custom_prefix}
                  onChange={(e) => onChange({ custom_prefix: e.target.value })}
                  className="input-field input-cyan"
                />
              </div>

              <div>
                <label className="field-label">Custom Output Filename</label>
                <input
                  type="text"
                  value={form.custom_filename}
                  onChange={(e) => onChange({ custom_filename: e.target.value })}
                  className="input-field input-cyan"
                />
              </div>

              <div>
                <label className="field-label">Playlist Item Limit</label>
                <input
                  type="number"
                  min="0"
                  value={form.item_limit}
                  onChange={(e) => onChange({ item_limit: Number(e.target.value) || 0 })}
                  className="input-field input-cyan"
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
