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

export interface AuthStatus {
  setup_required: boolean
}

export interface AuthResponse {
  token: string
  username: string
}

export interface User {
  id: number
  username: string
  created_at: string
}

export interface UsersResponse {
  users: User[]
}
