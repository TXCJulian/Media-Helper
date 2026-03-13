export interface EpisodeForm {
  series: string
  season: number
  directory: string
  dry_run: boolean
  assign_seq: boolean
  threshold: number
  lang: string
}

export interface MusicForm {
  artist: string
  album: string
  directory: string
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
  filename: string
  inPoint: number
  outPoint: number
  outputName: string
  streamCopy: boolean
  codec: string
  audioCodec: string
  container: string
  audioStreamIndex: number | null
}

export interface AudioStreamInfo {
  index: number
  codec: string
  channels: number
  sample_rate: number
  language: string
  title: string
}

export interface ProbeResult {
  duration: number
  video_codec: string | null
  audio_codec: string
  container: string
  bitrate: number
  width: number | null
  height: number | null
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
  audio_codec: string | null
  container: string | null
  output_name: string | null
  audio_stream_index: number | null
}

export interface CutterJob {
  job_id: string
  source: string
  original_name: string
  original_path: string
  created_at: string
  status: string
  output_files: string[]
  cut_settings?: CutJobSettings | null
  preview_transcoded?: boolean
  transcode_error?: string | null
}

export interface CutterPersistedState {
  form: CutterForm
  directories: string[]
  search: string
  serverState: CutterSourceState
  uploadState: CutterSourceState
}
