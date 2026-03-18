export interface DirectoryEntry {
  path: string
  base: string
}

export interface EpisodeForm {
  series: string
  season: number
  directory: string
  base: string
  dry_run: boolean
  assign_seq: boolean
  threshold: number
  lang: string
}

export interface MusicForm {
  artist: string
  album: string
  directory: string
  base: string
  dry_run: boolean
}

export interface DirectoriesResponse {
  directories: string[]
}

export interface RenameResponse {
  success?: boolean
  error?: string
  log?: string[]
  directories?: string[]
}

export interface LyricsForm {
  artist: string
  album: string
  directory: string
  base: string
  format: 'lrc' | 'txt' | 'all'
  skip_existing: boolean
  language: string
  no_separation: boolean
  no_correction: boolean
}

export interface TranscriberHealth {
  status: string
  gpu_backend?: string
  gpu_name?: string | null
  transcription_engine?: string
  error?: string
}

export interface MusicFileInfo {
  name: string
  has_lrc: boolean
  has_txt: boolean
}

export interface MusicFilesResponse {
  files: MusicFileInfo[]
  error?: string
}

export interface CutterForm {
  source: 'server' | 'upload'
  directory: string
  base: string
  filename: string
  inPoint: number
  outPoint: number
  outputName: string
  streamCopy: boolean
  codec: string
  container: string
  audioTracks: AudioTrackConfig[]
  keepQuality: boolean
}

export interface AudioStreamInfo {
  index: number
  codec: string
  channels: number
  sample_rate: number
  bit_rate: number
  language: string
  title: string
}

export interface AudioTrackConfig {
  streamIndex: number
  mode: 'passthru' | 'reencode' | 'remove'
  codec: string
}

export interface ProbeResult {
  duration: number
  video_codec: string | null
  audio_codec: string
  container: string
  bitrate: number
  video_bitrate: number | null
  width: number | null
  height: number | null
  display_aspect_ratio: string | null
  sample_rate: number
  needs_transcoding: boolean
  audio_streams: AudioStreamInfo[]
}

export interface CutterPreviewStatus {
  state: 'idle' | 'running' | 'done' | 'error'
  ready: boolean
  percent: number
  eta_seconds: number | null
  elapsed_seconds: number
  message: string
}

export interface CutterFileInfo {
  name: string
  size: number
  extension: string
}

export interface CutterSourceState {
  probe: ProbeResult | null
  peaks: number[]
  filePath: string
  fileId: string
  thumbnailUrl: string
  files: CutterFileInfo[]
  jobId: string
  outputFiles: string[]
  isLoadingFile: boolean
}

export interface CutJobSettings {
  in_point: number
  out_point: number
  stream_copy: boolean
  codec: string | null
  container: string | null
  audio_tracks: { index: number; mode: string; codec: string | null }[]
  keep_quality: boolean
  output_name: string | null
}

export interface CutterJob {
  job_id: string
  source: 'server' | 'upload'
  original_name: string
  original_path: string
  created_at: string
  status: 'uploading' | 'ready' | 'transcoding' | 'cutting' | 'done' | 'error'
  output_files: string[]
  cut_settings?: CutJobSettings | null
  preview_transcoded?: boolean
  browser_ready?: boolean
  transcode_error?: string | null
  base?: string
}

export interface CutterPersistedState {
  form: CutterForm
  directories: DirectoryEntry[]
  search: string
  serverState: CutterSourceState
  uploadState: CutterSourceState
}
