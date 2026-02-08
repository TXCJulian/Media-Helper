import { useCallback, useEffect, useState } from 'react'
import { fetchJson, postForm, postRefresh } from '@/lib/api'
import { useDebounce } from '@/hooks/useDebounce'
import type { DirectoriesResponse, EpisodeForm, RenameResponse } from '@/types'

interface EpisodePanelProps {
  onLog: (log: string[]) => void
  onError: (error: string) => void
}

export default function EpisodePanel({ onLog, onError }: EpisodePanelProps) {
  const [form, setForm] = useState<EpisodeForm>({
    series: '',
    season: 1,
    directory: '',
    dry_run: true,
    assign_seq: false,
    threshold: 0.75,
    lang: 'de',
  })
  const [directories, setDirectories] = useState<string[]>([])
  const [isLoadingDirs, setIsLoadingDirs] = useState(false)
  const [isRenaming, setIsRenaming] = useState(false)

  const debouncedSeries = useDebounce(form.series, 500)
  const debouncedSeason = useDebounce(form.season, 500)

  const fetchDirs = useCallback(
    async (series: string, season: number) => {
      setIsLoadingDirs(true)
      onError('')
      try {
        const params: Record<string, string> = {}
        if (series) params.series = series
        if (season) params.season = String(season)
        const data = await fetchJson<DirectoriesResponse>('/directories/tvshows', params)
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
    void fetchDirs('', 0)
  }, [fetchDirs])

  useEffect(() => {
    void fetchDirs(debouncedSeries, debouncedSeason)
  }, [debouncedSeries, debouncedSeason, fetchDirs])

  const handleRefresh = async () => {
    setIsLoadingDirs(true)
    onError('')
    try {
      await postRefresh()
    } catch (err) {
      onError(`Fehler beim Aktualisieren: ${err instanceof Error ? err.message : String(err)}`)
    }
    await fetchDirs(form.series, form.season)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsRenaming(true)
    onError('')
    onLog([])

    try {
      const data = await postForm<RenameResponse>('/rename/episodes', {
        series: form.series,
        season: form.season,
        directory: form.directory,
        dry_run: form.dry_run,
        assign_seq: form.assign_seq,
        threshold: form.threshold,
        lang: form.lang,
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

  const update = <K extends keyof EpisodeForm>(key: K, value: EpisodeForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const busy = isLoadingDirs || isRenaming

  return (
    <div className="flex flex-col">
      <h2 className="mb-3 text-center text-xl font-semibold">Episode Renamer</h2>
      <form
        onSubmit={(e) => void handleSubmit(e)}
        className="flex flex-1 flex-col rounded-lg bg-[#1e1e1e] p-5 shadow-md"
      >
        <div className="mb-2">
          <label className="block font-semibold text-[#bbb]">Serie:</label>
          <input
            type="text"
            value={form.series}
            onChange={(e) => update('series', e.target.value)}
            placeholder="Name der Serie"
            required
            className="mt-1 h-10 w-full rounded border border-[#333] bg-[#2a2a2a] px-2 text-[#e0e0e0]"
          />
        </div>

        <div className="mb-2">
          <label className="mt-4 block font-semibold text-[#bbb]">Staffel:</label>
          <input
            type="number"
            value={form.season}
            onChange={(e) => update('season', Number(e.target.value))}
            min={1}
            required
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

        <div className="mb-2">
          <label className="mt-4 block font-semibold text-[#bbb]">Sprache:</label>
          <select
            value={form.lang}
            onChange={(e) => update('lang', e.target.value)}
            className="mt-1 h-10 w-full rounded border border-[#333] bg-[#2a2a2a] px-2 text-[#e0e0e0]"
          >
            <option value="de">Deutsch</option>
            <option value="en">Englisch</option>
          </select>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <input
            type="checkbox"
            id="dry_run_ep"
            checked={form.dry_run}
            onChange={(e) => update('dry_run', e.target.checked)}
          />
          <label htmlFor="dry_run_ep" className="m-0 inline whitespace-nowrap">
            --dry-run
          </label>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <input
            type="checkbox"
            id="assign_seq"
            checked={form.assign_seq}
            onChange={(e) => update('assign_seq', e.target.checked)}
          />
          <label htmlFor="assign_seq" className="m-0 inline whitespace-nowrap">
            --assign-seq
          </label>
        </div>

        <div className="mb-2">
          <label className="mt-4 block font-semibold text-[#bbb]">Match Threshold:</label>
          <input
            type="number"
            value={form.threshold}
            onChange={(e) => update('threshold', Number(e.target.value))}
            step={0.05}
            min={0}
            max={1}
            className="mt-1 h-10 w-full rounded border border-[#333] bg-[#2a2a2a] px-2 text-[#e0e0e0]"
          />
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
