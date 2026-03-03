import { useCallback, useEffect, useState } from 'react'
import { fetchJson, postForm, postRefresh } from '@/lib/api'
import { useDebounce } from '@/hooks/useDebounce'
import type { DirectoriesResponse, MusicForm, RenameResponse } from '@/types'
import PanelLayout from '@/components/PanelLayout'
import LogPanel from '@/components/LogPanel'
import FormSection from '@/components/ui/FormSection'
import DirectorySelect from '@/components/ui/DirectorySelect'
import ToggleSwitch from '@/components/ui/ToggleSwitch'

interface MusicPanelProps {
  onLog: (log: string[]) => void
  onError: (error: string) => void
  onBack: () => void
  log: string[]
  error: string
  hasStarted: boolean
}

export default function MusicPanel({
  onLog,
  onError,
  onBack,
  log,
  error,
  hasStarted,
}: MusicPanelProps) {
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
        onError(
          `Fehler beim Laden der Verzeichnisse: ${err instanceof Error ? err.message : String(err)}`,
        )
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
    <PanelLayout title="Music Renamer" onBack={onBack}>
      <form onSubmit={(e) => void handleSubmit(e)}>
        <FormSection label="Search">
          <div className="flex gap-3">
            <div className="mb-3 flex-1">
              <label className="field-label">Künstler</label>
              <input
                type="text"
                value={form.artist}
                onChange={(e) => update('artist', e.target.value)}
                placeholder="Name des Künstlers"
                className="input-field input-indigo"
              />
            </div>
            <div className="mb-3 flex-1">
              <label className="field-label">Album</label>
              <input
                type="text"
                value={form.album}
                onChange={(e) => update('album', e.target.value)}
                placeholder="Name des Albums"
                className="input-field input-indigo"
              />
            </div>
          </div>
        </FormSection>

        <FormSection label="Directory">
          <DirectorySelect
            directories={directories}
            value={form.directory}
            onChange={(v) => update('directory', v)}
            onRefresh={() => void handleRefresh()}
            isLoading={isLoadingDirs}
            disabled={busy}
            color="indigo"
          />
        </FormSection>

        <FormSection label="Options">
          <ToggleSwitch
            checked={form.dry_run}
            onChange={(v) => update('dry_run', v)}
            disabled={busy}
            color="indigo"
            label="Dry Run"
          />
        </FormSection>

        <button type="submit" disabled={busy} className="btn-submit btn-indigo">
          {isRenaming ? <span className="spinner-md" /> : 'Umbenennen'}
        </button>

        <LogPanel
          log={log}
          error={error}
          hasStarted={hasStarted}
          color="indigo"
          idleMessage="Ready for renaming..."
        />
      </form>
    </PanelLayout>
  )
}
