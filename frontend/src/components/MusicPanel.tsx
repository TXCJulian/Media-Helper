import { useCallback, useEffect, useState } from 'react'
import { fetchJson, postForm, postRefresh } from '@/lib/api'
import { useDebounce } from '@/hooks/useDebounce'
import type { DirectoriesResponse, MusicForm, RenameResponse } from '@/types'

interface MusicPanelProps {
  onLog: (log: string[]) => void
  onError: (error: string) => void
}

export default function MusicPanel({ onLog, onError }: MusicPanelProps) {
  const [form, setForm] = useState<MusicForm>({
    artist: '',
    album: '',
    directory: '',
    dry_run: true,
  })
  const [directories, setDirectories] = useState<string[]>([])
  const [isLoadingDirs, setIsLoadingDirs] = useState(false)
  const [isRenaming, setIsRenaming] = useState(false)

  const debouncedArtist = useDebounce(form.artist, 500)
  const debouncedAlbum = useDebounce(form.album, 500)

  const fetchDirs = useCallback(
    async (artist: string, album: string) => {
      setIsLoadingDirs(true)
      onError('')
      try {
        const params: Record<string, string> = {}
        if (artist) params.artist = artist
        if (album) params.album = album
        const data = await fetchJson<DirectoriesResponse>('/directories/music', params)
        const dirs = data.directories ?? []
        setDirectories(dirs)
        setForm((prev) => ({
          ...prev,
          directory:
            dirs.length > 0 ? (dirs.includes(prev.directory) ? prev.directory : dirs[0]!) : '',
        }))
      } catch (err) {
        onError(`Fehler beim Laden der Verzeichnisse: ${err instanceof Error ? err.message : String(err)}`)
      } finally {
        setIsLoadingDirs(false)
      }
    },
    [onError],
  )

  useEffect(() => {
    void fetchDirs('', '')
  }, [fetchDirs])

  useEffect(() => {
    void fetchDirs(debouncedArtist, debouncedAlbum)
  }, [debouncedArtist, debouncedAlbum, fetchDirs])

  const handleRefresh = async () => {
    setIsLoadingDirs(true)
    onError('')
    try {
      await postRefresh()
    } catch (err) {
      onError(`Fehler beim Aktualisieren: ${err instanceof Error ? err.message : String(err)}`)
    }
    await fetchDirs(form.artist, form.album)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsRenaming(true)
    onError('')
    onLog([])

    try {
      const data = await postForm<RenameResponse>('/rename/music', {
        directory: form.directory,
        dry_run: form.dry_run,
      })
      if (data.error) onError(data.error)
      onLog(data.log ?? [])
      if (data.directories) setDirectories(data.directories)
    } catch (err) {
      onError(`Fehler beim Umbenennen: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setIsRenaming(false)
    }
  }

  const update = <K extends keyof MusicForm>(key: K, value: MusicForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const busy = isLoadingDirs || isRenaming

  return (
    <div className="flex flex-col">
      <h2 className="mb-3 text-center text-xl font-semibold">Music Renamer</h2>
      <form
        onSubmit={(e) => void handleSubmit(e)}
        className="flex flex-1 flex-col rounded-lg bg-[#1e1e1e] p-5 shadow-md"
      >
        <div className="mb-2">
          <label className="block font-semibold text-[#bbb]">Künstler:</label>
          <input
            type="text"
            value={form.artist}
            onChange={(e) => update('artist', e.target.value)}
            placeholder="Name des Künstlers"
            className="mt-1 h-10 w-full rounded border border-[#333] bg-[#2a2a2a] px-2 text-[#e0e0e0]"
          />
        </div>

        <div className="mb-2">
          <label className="mt-4 block font-semibold text-[#bbb]">Album:</label>
          <input
            type="text"
            value={form.album}
            onChange={(e) => update('album', e.target.value)}
            placeholder="Name des Albums"
            className="mt-1 h-10 w-full rounded border border-[#333] bg-[#2a2a2a] px-2 text-[#e0e0e0]"
          />
        </div>

        <div className="mb-2">
          <label className="mt-4 block font-semibold text-[#bbb]">Verzeichnis:</label>
          <select
            value={form.directory}
            onChange={(e) => update('directory', e.target.value)}
            disabled={busy}
            required
            className="mt-1 h-10 w-full rounded border border-[#333] bg-[#2a2a2a] px-2 text-[#e0e0e0]"
          >
            {directories.length > 0 ? (
              directories.map((dir) => (
                <option key={dir} value={dir}>
                  {dir}
                </option>
              ))
            ) : (
              <option disabled value="">
                Keine Ordner gefunden
              </option>
            )}
          </select>
          <button
            type="button"
            onClick={() => void handleRefresh()}
            disabled={busy}
            className="relative mt-5 inline-flex h-10 w-full items-center justify-center rounded bg-blue-600 px-4 text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {isLoadingDirs ? <span className="spinner-sm" /> : 'Verzeichnisse neu laden'}
          </button>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <input
            type="checkbox"
            id="dry_run_mu"
            checked={form.dry_run}
            onChange={(e) => update('dry_run', e.target.checked)}
          />
          <label htmlFor="dry_run_mu" className="m-0 inline whitespace-nowrap">
            --dry-run
          </label>
        </div>

        <div className="mt-auto">
          <button
            type="submit"
            disabled={busy}
            className="relative mt-5 inline-flex h-10 w-full items-center justify-center rounded bg-blue-600 px-4 text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {isRenaming ? <span className="spinner-md" /> : 'Umbenennen'}
          </button>
        </div>
      </form>
    </div>
  )
}
