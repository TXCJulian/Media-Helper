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
