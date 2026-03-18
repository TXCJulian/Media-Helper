import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchJson, fetchTranscriberHealth, fetchMusicFiles, postRefresh } from '@/lib/api'
import { connectSSE } from '@/lib/sse'
import { useDebounce } from '@/hooks/useDebounce'
import type {
  DirectoriesResponse,
  DirectoryEntry,
  LyricsForm,
  TranscriberHealth,
  MusicFileInfo,
} from '@/types'
import PanelLayout from '@/components/PanelLayout'
import LogPanel from '@/components/LogPanel'
import FormSection from '@/components/ui/FormSection'
import DirectorySelect from '@/components/ui/DirectorySelect'
import SegmentedControl from '@/components/ui/SegmentedControl'
import ToggleSwitch from '@/components/ui/ToggleSwitch'

interface LyricsPanelProps {
  onLog: (log: string[]) => void
  onError: (error: string) => void
  onBack: () => void
  log: string[]
  error: string
  hasStarted: boolean
  showBaseLabel?: boolean
}

function shortGpuName(gpu: string | null | undefined): string {
  if (!gpu) return ''
  return gpu.replace(/^NVIDIA GeForce /, '')
}

export default function LyricsPanel({
  onLog,
  onError,
  onBack,
  log,
  error,
  hasStarted,
  showBaseLabel,
}: LyricsPanelProps) {
  const [form, setForm] = useState<LyricsForm>({
    artist: '',
    album: '',
    directory: '',
    base: '',
    format: 'lrc',
    skip_existing: true,
    language: '',
    no_separation: false,
    no_correction: false,
  })
  const [directories, setDirectories] = useState<DirectoryEntry[]>([])
  const [isLoadingDirs, setIsLoadingDirs] = useState(false)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [health, setHealth] = useState<TranscriberHealth | null>(null)
  const [musicFiles, setMusicFiles] = useState<MusicFileInfo[]>([])
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set())
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const abortSSERef = useRef<(() => void) | null>(null)

  const debouncedArtist = useDebounce(form.artist, 500)
  const debouncedAlbum = useDebounce(form.album, 500)

  const [isCheckingHealth, setIsCheckingHealth] = useState(false)

  const checkHealth = useCallback(() => {
    setIsCheckingHealth(true)
    setHealth(null)
    fetchTranscriberHealth()
      .then(setHealth)
      .catch(() => setHealth({ status: 'unreachable', error: 'Could not reach backend' }))
      .finally(() => setIsCheckingHealth(false))
  }, [])

  useEffect(() => {
    checkHealth()
  }, [checkHealth])

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
        setForm((prev) => {
          const stillPresent = dirs.some(
            (d) => d.path === prev.directory && d.base === prev.base,
          )
          return {
            ...prev,
            directory: dirs.length > 0 ? (stillPresent ? prev.directory : dirs[0]!.path) : '',
            base: dirs.length > 0 ? (stillPresent ? prev.base : dirs[0]!.base) : '',
          }
        })
      } catch (err) {
        onError(`Error loading directories: ${err instanceof Error ? err.message : String(err)}`)
      } finally {
        setIsLoadingDirs(false)
      }
    },
    [onError],
  )

  useEffect(() => {
    void fetchDirs(debouncedArtist, debouncedAlbum)
  }, [debouncedArtist, debouncedAlbum, fetchDirs])

  const loadFiles = useCallback(
    (directory: string, base: string, selectAll: boolean, signal?: { cancelled: boolean }) => {
      setIsLoadingFiles(true)
      fetchMusicFiles(directory, base)
        .then((data) => {
          if (signal?.cancelled) return
          const files = data.files ?? []
          setMusicFiles(files)
          setSelectedFiles((prev) =>
            selectAll
              ? new Set(files.map((f) => f.name))
              : new Set([...prev].filter((n) => new Set(files.map((f) => f.name)).has(n))),
          )
        })
        .catch(() => {
          if (signal?.cancelled) return
          setMusicFiles([])
          setSelectedFiles(new Set())
        })
        .finally(() => {
          if (!signal?.cancelled) setIsLoadingFiles(false)
        })
    },
    [],
  )

  const refreshFiles = useCallback(() => {
    if (!form.directory) return
    loadFiles(form.directory, form.base, false)
  }, [form.directory, form.base, loadFiles])

  useEffect(() => {
    if (!form.directory) {
      setMusicFiles([])
      setSelectedFiles(new Set())
      return
    }
    const signal = { cancelled: false }
    loadFiles(form.directory, form.base, true, signal)
    return () => {
      signal.cancelled = true
    }
  }, [form.directory, form.base, loadFiles])

  const handleRefresh = async () => {
    setIsLoadingDirs(true)
    onError('')
    try {
      await postRefresh()
    } catch (err) {
      onError(`Error refreshing: ${err instanceof Error ? err.message : String(err)}`)
    }
    await fetchDirs(form.artist, form.album)
  }

  const toggleFile = (name: string) => {
    setSelectedFiles((prev) => {
      const next = new Set(prev)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
  }

  const selectAll = () => setSelectedFiles(new Set(musicFiles.map((f) => f.name)))
  const deselectAll = () => setSelectedFiles(new Set())

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (selectedFiles.size === 0 || isTranscribing) return

    setIsTranscribing(true)
    onError('')
    onLog([])

    const logs: string[] = []

    const params: Record<string, string> = {
      directory: form.directory,
      base: form.base,
      output_format: form.format,
      skip_existing: String(form.skip_existing),
      no_separation: String(form.no_separation),
      no_correction: String(form.no_correction),
    }
    if (form.language) params.language = form.language

    if (selectedFiles.size < musicFiles.length) {
      params.files = Array.from(selectedFiles).join(',')
    }

    abortSSERef.current?.()
    abortSSERef.current = connectSSE('/transcribe/start', params, {
      onProgress: (data) => {
        if (data === 'heartbeat') return
        logs.push(data)
        onLog([...logs])
      },
      onError: (data) => {
        logs.push(`[ERROR]\t\t\t${data}`)
        onLog([...logs])
      },
      onDone: (data) => {
        logs.push(`\n${data}`)
        onLog([...logs])
        setIsTranscribing(false)
        abortSSERef.current = null
        refreshFiles()
      },
    })
  }

  // Abort SSE stream on unmount
  useEffect(() => {
    return () => {
      abortSSERef.current?.()
    }
  }, [])

  const update = <K extends keyof LyricsForm>(key: K, value: LyricsForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const isServiceOk = health?.status === 'ok'
  const busy = isLoadingDirs || isTranscribing

  const healthPill = (
    <button
      type="button"
      onClick={checkHealth}
      disabled={isTranscribing || isCheckingHealth}
      title={isServiceOk ? 'Click to re-check connection' : 'Click to retry connection'}
      className="ml-auto inline-flex cursor-pointer items-center gap-[0.4rem] rounded-[20px] border border-[var(--border)] bg-[var(--bg-input)] px-[0.7rem] py-[0.3rem] text-[0.7rem] font-medium text-[var(--text-tertiary)] transition-all duration-200 hover:border-[var(--glass-border-hover)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-secondary)] disabled:cursor-not-allowed disabled:opacity-70"
    >
      <span
        className={`h-[7px] w-[7px] shrink-0 rounded-full transition-colors duration-200 ${
          health === null
            ? 'animate-pulse bg-yellow-500'
            : isServiceOk
              ? 'bg-[var(--success)] shadow-[0_0_8px_var(--success-glow)]'
              : 'bg-[var(--error)]'
        }`}
      />
      {isServiceOk && health?.gpu_name
        ? shortGpuName(health.gpu_name)
        : health === null
          ? 'Checking...'
          : 'Offline'}
    </button>
  )

  return (
    <PanelLayout title="Lyrics Transcriber" onBack={onBack} rightElement={healthPill}>
      <form onSubmit={handleSubmit}>
        <FormSection label="Search">
          <div className="flex gap-3">
            <div className="mb-3 flex-1">
              <label className="field-label">Artist</label>
              <input
                type="text"
                value={form.artist}
                onChange={(e) => update('artist', e.target.value)}
                placeholder="Artist name"
                className="input-field input-rose"
              />
            </div>
            <div className="mb-3 flex-1">
              <label className="field-label">Album</label>
              <input
                type="text"
                value={form.album}
                onChange={(e) => update('album', e.target.value)}
                placeholder="Album name"
                className="input-field input-rose"
              />
            </div>
          </div>
        </FormSection>

        <FormSection label="Directory">
          <DirectorySelect
            directories={directories}
            value={form.directory}
            base={form.base}
            onChange={(val, base) => setForm((prev) => ({ ...prev, directory: val, base }))}
            onRefresh={() => void handleRefresh()}
            isLoading={isLoadingDirs}
            disabled={busy}
            color="rose"
            showBaseLabel={showBaseLabel}
          />
        </FormSection>

        {/* File List */}
        {form.directory && (
          <FormSection label="Songs">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[0.8rem] text-[var(--text-secondary)]">
                {selectedFiles.size} / {musicFiles.length} selected
              </span>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={refreshFiles}
                  disabled={isLoadingFiles}
                  title="Refresh file list"
                  className="flex h-[26px] w-[26px] cursor-pointer items-center justify-center rounded-[7px] border border-[var(--border)] bg-[var(--bg-input)] text-[0.8rem] text-[var(--text-secondary)] transition-all duration-200 hover:border-[var(--glass-border-hover)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {isLoadingFiles ? <span className="spinner-sm" /> : '↻'}
                </button>
                <button
                  type="button"
                  onClick={selectAll}
                  disabled={busy}
                  className="cursor-pointer border-none bg-none font-[Geist,sans-serif] text-[0.68rem] font-semibold uppercase tracking-[0.06em] text-[var(--accent-3)] transition-opacity duration-200 hover:opacity-70"
                >
                  All
                </button>
                <button
                  type="button"
                  onClick={deselectAll}
                  disabled={busy}
                  className="cursor-pointer border-none bg-none font-[Geist,sans-serif] text-[0.68rem] font-semibold uppercase tracking-[0.06em] text-[var(--accent-3)] transition-opacity duration-200 hover:opacity-70"
                >
                  None
                </button>
              </div>
            </div>
            <div className="max-h-[210px] overflow-y-auto rounded-[10px] border border-[var(--border)] bg-[var(--bg-input)] p-[0.4rem]">
              {isLoadingFiles ? (
                <div className="py-4 text-center text-[0.8rem] text-[var(--text-tertiary)]">
                  Loading files...
                </div>
              ) : musicFiles.length === 0 ? (
                <div className="py-4 text-center text-[0.8rem] text-[var(--text-tertiary)]">
                  No music files found
                </div>
              ) : (
                musicFiles.map((file) => (
                  <label
                    key={file.name}
                    className="flex cursor-pointer items-center gap-[0.6rem] rounded-lg px-2 py-[0.4rem] transition-colors duration-150 hover:bg-[rgba(255,255,255,0.025)]"
                  >
                    <input
                      type="checkbox"
                      checked={selectedFiles.has(file.name)}
                      onChange={() => toggleFile(file.name)}
                      disabled={busy}
                      className="shrink-0 accent-[var(--accent-3)]"
                    />
                    <span className="min-w-0 flex-1 truncate text-[0.8rem] text-[var(--text-primary)]">
                      {file.name}
                    </span>
                    <span className="flex shrink-0 gap-1">
                      {file.has_lrc && (
                        <span className="rounded-[5px] bg-[var(--success-glow)] px-[0.4rem] py-[0.15rem] text-[0.56rem] font-semibold uppercase tracking-[0.04em] text-[var(--success)]">
                          LRC
                        </span>
                      )}
                      {file.has_txt && (
                        <span className="rounded-[5px] bg-[rgba(96,165,250,0.15)] px-[0.4rem] py-[0.15rem] text-[0.56rem] font-semibold uppercase tracking-[0.04em] text-[#60a5fa]">
                          TXT
                        </span>
                      )}
                    </span>
                  </label>
                ))
              )}
            </div>
          </FormSection>
        )}

        <FormSection label="Options">
          <div className="mb-3">
            <label className="field-label">Format</label>
            <SegmentedControl
              options={[
                { label: 'LRC', value: 'lrc' },
                { label: 'TXT', value: 'txt' },
                { label: 'Both', value: 'all' },
              ]}
              value={form.format}
              onChange={(v) => update('format', v as LyricsForm['format'])}
              disabled={busy}
              color="rose"
            />
          </div>

          <div className="mt-2">
            <ToggleSwitch
              checked={form.skip_existing}
              onChange={(v) => update('skip_existing', v)}
              disabled={busy}
              color="rose"
              label="Skip Existing Lyrics"
            />
          </div>

          <div className="mt-[0.85rem]">
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex cursor-pointer items-center gap-2 border-none bg-none p-0 font-[Geist,sans-serif] text-[0.75rem] text-[var(--text-tertiary)] transition-colors duration-200 hover:text-[var(--text-secondary)]"
            >
              <span
                className={`text-[0.55rem] transition-transform duration-200 ${showAdvanced ? 'rotate-90' : ''}`}
              >
                ▶
              </span>
              Advanced Options
            </button>

            {showAdvanced && (
              <div className="mt-3 rounded-[10px] border border-[var(--border)] bg-[rgba(0,0,0,0.2)] p-4">
                <div className="mb-[0.65rem]">
                  <label className="field-label">Language (empty = Auto)</label>
                  <input
                    type="text"
                    value={form.language}
                    onChange={(e) => update('language', e.target.value)}
                    placeholder="e.g. de, en, ja"
                    disabled={busy}
                    className="input-field input-rose !h-9 !text-[0.8rem]"
                  />
                </div>
                <div className="flex flex-col gap-[0.4rem]">
                  <ToggleSwitch
                    checked={form.no_separation}
                    onChange={(v) => update('no_separation', v)}
                    disabled={busy}
                    color="rose"
                    label="Skip Vocal Separation"
                  />
                  <ToggleSwitch
                    checked={form.no_correction}
                    onChange={(v) => update('no_correction', v)}
                    disabled={busy}
                    color="rose"
                    label="Skip Genius Correction"
                  />
                </div>
              </div>
            )}
          </div>
        </FormSection>

        <button
          type="submit"
          disabled={busy || !isServiceOk || selectedFiles.size === 0}
          className="btn-submit btn-rose"
        >
          {isTranscribing ? <span className="spinner-md" /> : 'Transcribe'}
        </button>

        <LogPanel
          log={log}
          error={error}
          hasStarted={hasStarted}
          color="rose"
          idleMessage="Ready for transcription..."
        />
      </form>
    </PanelLayout>
  )
}
