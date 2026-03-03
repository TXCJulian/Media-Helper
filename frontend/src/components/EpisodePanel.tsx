import { useCallback, useEffect, useState } from 'react'
import { fetchJson, postForm, postRefresh } from '@/lib/api'
import { useDebounce } from '@/hooks/useDebounce'
import type { DirectoriesResponse, EpisodeForm, RenameResponse } from '@/types'
import PanelLayout from '@/components/PanelLayout'
import LogPanel from '@/components/LogPanel'
import FormSection from '@/components/ui/FormSection'
import DirectorySelect from '@/components/ui/DirectorySelect'
import ToggleSwitch from '@/components/ui/ToggleSwitch'
import SegmentedControl from '@/components/ui/SegmentedControl'

interface EpisodePanelProps {
  onLog: (log: string[]) => void
  onError: (error: string) => void
  onBack: () => void
  log: string[]
  error: string
  hasStarted: boolean
}

export default function EpisodePanel({
  onLog,
  onError,
  onBack,
  log,
  error,
  hasStarted,
}: EpisodePanelProps) {
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
    <PanelLayout title="Episode Renamer" onBack={onBack}>
      <form onSubmit={(e) => void handleSubmit(e)}>
        <FormSection label="Search">
          <div className="flex gap-3">
            <div className="mb-3 flex-1">
              <label className="field-label">Serie</label>
              <input
                type="text"
                value={form.series}
                onChange={(e) => update('series', e.target.value)}
                placeholder="Name der Serie"
                required
                className="input-field input-blue"
              />
            </div>
            <div className="mb-3 w-28">
              <label className="field-label">Staffel</label>
              <input
                type="number"
                value={form.season}
                onChange={(e) => update('season', Number(e.target.value))}
                min={1}
                required
                className="input-field input-blue"
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
            color="blue"
          />
        </FormSection>

        <FormSection label="Options">
          <div className="mb-3">
            <label className="field-label">Sprache</label>
            <SegmentedControl
              options={[
                { label: 'Deutsch', value: 'de' },
                { label: 'English', value: 'en' },
              ]}
              value={form.lang}
              onChange={(v) => update('lang', v)}
              disabled={busy}
              color="blue"
            />
          </div>

          <div className="mb-3">
            <label className="field-label">Match Threshold</label>
            <input
              type="number"
              value={form.threshold}
              onChange={(e) => update('threshold', Number(e.target.value))}
              step={0.05}
              min={0}
              max={1}
              className="input-field input-blue !w-28"
            />
          </div>

          <div className="mt-2">
            <ToggleSwitch
              checked={form.dry_run}
              onChange={(v) => update('dry_run', v)}
              disabled={busy}
              color="blue"
              label="Dry Run"
            />
          </div>
          <div className="mt-[0.4rem]">
            <ToggleSwitch
              checked={form.assign_seq}
              onChange={(v) => update('assign_seq', v)}
              disabled={busy}
              color="blue"
              label="Assign Sequential (SxxExx fallback)"
            />
          </div>
        </FormSection>

        <button type="submit" disabled={busy} className="btn-submit btn-blue">
          {isRenaming ? <span className="spinner-md" /> : 'Umbenennen'}
        </button>

        <LogPanel
          log={log}
          error={error}
          hasStarted={hasStarted}
          color="blue"
          idleMessage="Ready for renaming..."
        />
      </form>
    </PanelLayout>
  )
}
