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
  container: string
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
}

export interface CutterFileInfo {
  name: string
  size: number
  extension: string
}
