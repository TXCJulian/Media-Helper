import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import PanelLayout from './PanelLayout'
import DirectorySelect from './ui/DirectorySelect'
import StyledSelect from './ui/StyledSelect'
import ToggleSwitch from './ui/ToggleSwitch'
import {
  createDownloadJob,
  deleteCookies,
  deleteDownloadJob,
  fetchDownloadJobs,
  fetchDownloaderStatus,
  fetchMediaDirectories,
  getDownloaderFileUrl,
  postCookies,
  postDownload,
  startDownloadJob,
} from '@/lib/api'
import type { DirectoryEntry, DownloadForm, DownloadJob, DownloaderStatus } from '@/types'

interface DownloaderPanelProps {
  onLog: (log: string[]) => void
  onError: (err: string) => void
  onBack: () => void
  log: string[]
  error: string
  showBaseLabel?: boolean
}

const STORAGE_KEY = 'downloader-settings'

const CODEC_OPTIONS: Record<string, { label: string; value: string }[]> = {
  video: [
    { label: 'Auto', value: 'auto' },
    { label: 'H.264', value: 'h264' },
    { label: 'H.265', value: 'h265' },
    { label: 'VP9', value: 'vp9' },
    { label: 'AV1', value: 'av1' },
  ],
  audio: [
    { label: 'Auto', value: 'auto' },
    { label: 'MP3', value: 'mp3' },
    { label: 'FLAC', value: 'flac' },
    { label: 'AAC', value: 'aac' },
    { label: 'Opus', value: 'opus' },
    { label: 'WAV', value: 'wav' },
  ],
  thumbnail: [],
}

const FORMAT_OPTIONS: Record<string, { label: string; value: string }[]> = {
  video: [
    { label: 'Auto', value: 'auto' },
    { label: 'MP4', value: 'mp4' },
    { label: 'MKV', value: 'mkv' },
    { label: 'WebM', value: 'webm' },
    { label: 'MOV', value: 'mov' },
  ],
  audio: [
    { label: 'Auto', value: 'auto' },
    { label: 'MP3', value: 'mp3' },
    { label: 'M4A', value: 'm4a' },
    { label: 'FLAC', value: 'flac' },
    { label: 'Opus', value: 'opus' },
    { label: 'WAV', value: 'wav' },
  ],
  thumbnail: [
    { label: 'Auto', value: 'auto' },
    { label: 'JPG', value: 'jpg' },
    { label: 'PNG', value: 'png' },
    { label: 'WebP', value: 'webp' },
  ],
}

const VIDEO_QUALITY = [
  { label: 'Best', value: 'best' },
  { label: '2160p', value: '2160p' },
  { label: '1440p', value: '1440p' },
  { label: '1080p', value: '1080p' },
  { label: '720p', value: '720p' },
  { label: '480p', value: '480p' },
  { label: 'Worst', value: 'worst' },
]

const AUDIO_QUALITY = [
  { label: 'Best', value: 'best' },
  { label: '320kbps', value: '320kbps' },
  { label: '256kbps', value: '256kbps' },
  { label: '192kbps', value: '192kbps' },
  { label: '128kbps', value: '128kbps' },
  { label: '96kbps', value: '96kbps' },
  { label: 'Worst', value: 'worst' },
]

const DEFAULT_FORM: Omit<DownloadForm, 'url'> = {
  type: 'video',
  codec: 'auto',
  format: 'auto',
  quality: 'best',
  output_dir: '',
  base: '',
  auto_start: true,
  sub_folder: '',
  custom_prefix: '',
  custom_filename: '',
  item_limit: 0,
  split_chapters: false,
}

function loadSettings(): Omit<DownloadForm, 'url'> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_FORM
    const parsed = JSON.parse(raw)
    return { ...DEFAULT_FORM, ...parsed }
  } catch {
    return DEFAULT_FORM
  }
}

function saveSettings(form: DownloadForm) {
  const { url: _, ...settings } = form
  localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
}

function parseDownloadEvent(data: string): Partial<DownloadJob> | null {
  if (!data || data.includes('"status": "heartbeat"') || data.includes('"status":"heartbeat"')) {
    return null
  }
  try {
    return JSON.parse(data) as Partial<DownloadJob>
  } catch {
    return null
  }
}

function mergeJob(prevJobs: DownloadJob[], patch: Partial<DownloadJob> | null): DownloadJob[] {
  if (!patch?.job_id) return prevJobs
  const nextJob: DownloadJob = {
    job_id: patch.job_id,
    url: patch.url ?? '',
    status: patch.status ?? 'queued',
    progress: typeof patch.progress === 'number' ? patch.progress : 0,
    speed: patch.speed ?? null,
    eta: patch.eta ?? null,
    filename: patch.filename ?? null,
    error: patch.error ?? null,
    created_at: patch.created_at ?? new Date().toISOString(),
    size: patch.size ?? null,
  }

  const idx = prevJobs.findIndex((job) => job.job_id === patch.job_id)
  if (idx === -1) return [nextJob, ...prevJobs]

  const prev = prevJobs[idx]!
  const merged: DownloadJob = {
    ...prev,
    ...patch,
    progress: typeof patch.progress === 'number' ? patch.progress : (prev.progress ?? 0),
    speed: 'speed' in patch ? (patch.speed ?? null) : prev.speed,
    eta: 'eta' in patch ? (patch.eta ?? null) : prev.eta,
    filename: 'filename' in patch ? (patch.filename ?? null) : prev.filename,
    error: 'error' in patch ? (patch.error ?? null) : prev.error,
    size: 'size' in patch ? (patch.size ?? null) : prev.size,
    job_id: patch.job_id,
    url: patch.url ?? prev.url,
    status: patch.status ?? prev.status,
    created_at: patch.created_at ?? prev.created_at,
  }

  return prevJobs.map((job, jobIdx) => (jobIdx === idx ? merged : job))
}

function formatProgress(progress: unknown): string {
  const n = typeof progress === 'number' ? progress : Number(progress) || 0
  return n.toFixed(1)
}

export default function DownloaderPanel({
  onLog,
  onError,
  onBack,
  log,
  error,
  showBaseLabel,
}: DownloaderPanelProps) {
  const [status, setStatus] = useState<DownloaderStatus | null>(null)
  const [directories, setDirectories] = useState<DirectoryEntry[]>([])
  const [jobs, setJobs] = useState<DownloadJob[]>([])
  const [form, setForm] = useState<DownloadForm>(() => ({
    url: '',
    ...loadSettings(),
  }))
  const [isBulkModalOpen, setIsBulkModalOpen] = useState(false)
  const [bulkUrls, setBulkUrls] = useState('')
  const [isRefreshingJobs, setIsRefreshingJobs] = useState(false)
  const [isRefreshingDirs, setIsRefreshingDirs] = useState(false)
  const [localError, setLocalError] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const logRef = useRef(log)
  logRef.current = log
  const sseAbortRef = useRef<Set<() => void>>(new Set())
  const lastJobStatusRef = useRef<Map<string, string>>(new Map())

  // Persist settings on form change (except url), debounced
  useEffect(() => {
    const id = window.setTimeout(() => saveSettings(form), 300)
    return () => window.clearTimeout(id)
  }, [form])

  const refreshStatus = useCallback(async () => {
    const nextStatus = await fetchDownloaderStatus()
    setStatus(nextStatus)
  }, [])

  const refreshDirectories = useCallback(async () => {
    setIsRefreshingDirs(true)
    try {
      const response = await fetchMediaDirectories()
      setDirectories(response.directories)
    } finally {
      setIsRefreshingDirs(false)
    }
  }, [])

  const refreshJobs = useCallback(async () => {
    setIsRefreshingJobs(true)
    try {
      const response = await fetchDownloadJobs()
      setJobs(response.jobs)
    } finally {
      setIsRefreshingJobs(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    Promise.allSettled([refreshStatus(), refreshDirectories(), refreshJobs()]).then((results) => {
      if (cancelled) return
      const rejected = results.find((result) => result.status === 'rejected')
      if (rejected?.status === 'rejected') {
        onError(
          rejected.reason instanceof Error ? rejected.reason.message : 'Failed to load downloader',
        )
      }
    })

    const interval = setInterval(() => {
      refreshJobs().catch((err) => {
        onError(err instanceof Error ? err.message : 'Failed to refresh downloads')
      })
    }, 5000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [onError, refreshDirectories, refreshJobs, refreshStatus])

  const handleTypeChange = (type: string) => {
    const nextType = type as DownloadForm['type']
    setForm((prev) => ({
      ...prev,
      type: nextType,
      codec: 'auto',
      format: 'auto',
      quality: 'best',
    }))
  }

  const applyEventPatch = useCallback(
    (data: string) => {
      const patch = parseDownloadEvent(data)
      if (!patch) return
      setJobs((prev) => mergeJob(prev, patch))
      if (patch.job_id && patch.status) {
        const prev = lastJobStatusRef.current.get(patch.job_id)
        if (prev !== patch.status) {
          lastJobStatusRef.current.set(patch.job_id, patch.status)
          const newLog = [...logRef.current, `${patch.status}: ${patch.filename ?? patch.url}`]
          logRef.current = newLog
          onLog(newLog)
        }
      }
    },
    [onLog],
  )

  const startTrackedSSE = useCallback(
    (
      start: (
        callbacks: {
          onProgress: (data: string) => void
          onError: (data: string) => void
          onDone: (data: string) => void
        },
      ) => () => void,
      callbacks: {
        onProgress: (data: string) => void
        onError: (data: string) => void
        onDone: (data: string) => void
      },
    ) => {
      let abort: (() => void) | null = null
      const cleanup = () => {
        if (abort) sseAbortRef.current.delete(abort)
      }
      abort = start({
        onProgress: callbacks.onProgress,
        onError: (data) => {
          cleanup()
          callbacks.onError(data)
        },
        onDone: (data) => {
          cleanup()
          callbacks.onDone(data)
        },
      })
      sseAbortRef.current.add(abort)
    },
    [],
  )

  useEffect(() => {
    return () => {
      sseAbortRef.current.forEach((abort) => abort())
      sseAbortRef.current.clear()
    }
  }, [])

  const sseCallbacks = useMemo(
    () => ({
      onProgress: (data: string) => applyEventPatch(data),
      onError: (data: string) => {
        const patch = parseDownloadEvent(data)
        if (patch) {
          setJobs((prev) => mergeJob(prev, patch))
          if (patch.error) onError(patch.error)
          return
        }
        onError(data)
      },
      onDone: (data: string) => {
        applyEventPatch(data)
        refreshJobs().catch(() => {})
      },
    }),
    [applyEventPatch, onError, refreshJobs],
  )

  const handleStartDownload = async (urlOverride?: string) => {
    const url = (urlOverride ?? form.url).trim()
    if (!url) {
      setLocalError('Please enter a URL')
      return
    }

    setLocalError('')
    onError('')
    const newLog = [...logRef.current, `queued: ${url}`]
    logRef.current = newLog
    onLog(newLog)

    if (!form.auto_start) {
      try {
        const job = await createDownloadJob({ ...form, url })
        setJobs((prev) => mergeJob(prev, job))
      } catch (err) {
        onError(err instanceof Error ? err.message : 'Failed to create download job')
      }
    } else {
      startTrackedSSE((cb) => postDownload({ ...form, url }, cb), sseCallbacks)
    }

    if (!urlOverride) setForm((prev) => ({ ...prev, url: '' }))
  }

  const handleStartQueuedJob = (jobId: string) => {
    startTrackedSSE((cb) => startDownloadJob(jobId, cb), sseCallbacks)
  }

  const handleBulkSubmit = () => {
    const urls = bulkUrls
      .split('\n')
      .map((v) => v.trim())
      .filter(Boolean)
    urls.forEach((url) => void handleStartDownload(url))
    setBulkUrls('')
    setIsBulkModalOpen(false)
  }

  const handleCookieUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      await postCookies(file)
      await refreshStatus()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to upload cookies')
    } finally {
      event.target.value = ''
    }
  }

  const handleCookieDelete = async () => {
    try {
      await deleteCookies()
      await refreshStatus()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to delete cookies')
    }
  }

  const handleDeleteJob = async (jobId: string) => {
    try {
      await deleteDownloadJob(jobId)
      setJobs((prev) => prev.filter((job) => job.job_id !== jobId))
      await refreshJobs()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to remove download')
    }
  }

  const handleClearCompleted = async () => {
    const completed = jobs.filter((j) => j.status === 'done')
    await Promise.allSettled(completed.map((j) => deleteDownloadJob(j.job_id)))
    await refreshJobs()
  }

  const handleClearFailed = async () => {
    const failed = jobs.filter((j) => j.status === 'error')
    await Promise.allSettled(failed.map((j) => deleteDownloadJob(j.job_id)))
    await refreshJobs()
  }

  const handleRetryFailed = () => {
    const failed = jobs.filter((j) => j.status === 'error')
    failed.forEach((j) => void handleStartDownload(j.url))
  }

  const activeDownloads = useMemo(
    () => jobs.filter((job) => ['queued', 'downloading', 'processing'].includes(job.status)),
    [jobs],
  )

  const completedDownloads = useMemo(
    () =>
      jobs
        .filter((job) => ['done', 'error'].includes(job.status))
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [jobs],
  )

  const qualityOptions = form.type === 'audio' ? AUDIO_QUALITY : VIDEO_QUALITY

  return (
    <PanelLayout title="Downloader" onBack={onBack} maxWidth="920px">
      <div className="space-y-6">
        {/* ── URL Hero ── */}
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            type="text"
            value={form.url}
            placeholder="URL"
            onChange={(e) => setForm((prev) => ({ ...prev, url: e.target.value }))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleStartDownload()
            }}
            className="input-field input-amber flex-1"
          />
          <div className="flex gap-2 sm:shrink-0">
            <button
              type="button"
              onClick={() => void handleStartDownload()}
              className="btn-submit btn-amber h-[42px] flex-1 !w-auto px-5 text-[0.85rem] sm:flex-none"
            >
              Download
            </button>
            <button
              type="button"
              onClick={() => setIsBulkModalOpen(true)}
              className="h-[42px] flex-1 rounded-[10px] border border-[var(--glass-border)] bg-[var(--bg-glass)] px-5 text-[0.85rem] font-medium text-[var(--text-secondary)] transition-all hover:border-[var(--glass-border-hover)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] sm:flex-none"
            >
              Bulk Add
            </button>
          </div>
        </div>

        {(localError || error) && (
          <div
            className="flex items-center justify-between rounded-lg border border-red-500/15 bg-red-500/[0.06] px-4 py-2.5"
            role="alert"
          >
            <p className="text-[0.8rem] text-red-400">{localError || error}</p>
            <button
              type="button"
              className="ml-3 shrink-0 text-[0.7rem] text-red-400/50"
              onClick={() => {
                setLocalError('')
                onError('')
              }}
              aria-label="Dismiss error"
            >
              dismiss
            </button>
          </div>
        )}

        {/* ── Quick Settings Row ── */}
        <div
          className={`grid gap-3 ${form.type === 'thumbnail' ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-4'}`}
        >
          <StyledSelect
            label="Type"
            options={[
              { label: 'Video', value: 'video' },
              { label: 'Audio', value: 'audio' },
              { label: 'Thumbnail', value: 'thumbnail' },
            ]}
            value={form.type}
            onChange={handleTypeChange}
          />

          {form.type !== 'thumbnail' && (
            <StyledSelect
              label="Codec"
              options={CODEC_OPTIONS[form.type] ?? []}
              value={form.codec}
              onChange={(v) => setForm((prev) => ({ ...prev, codec: v }))}
            />
          )}

          <StyledSelect
            label="Format"
            options={FORMAT_OPTIONS[form.type] ?? []}
            value={form.format}
            onChange={(v) => setForm((prev) => ({ ...prev, format: v }))}
          />

          {form.type !== 'thumbnail' && (
            <StyledSelect
              label="Quality"
              options={qualityOptions}
              value={form.quality}
              onChange={(v) => setForm((prev) => ({ ...prev, quality: v }))}
            />
          )}
        </div>

        {/* ── Advanced Options (collapsible) ── */}
        <div className="rounded-[14px] border border-white/6 bg-white/[0.02]">
          <button
            type="button"
            onClick={() => setAdvancedOpen((prev) => !prev)}
            className="flex w-full items-center justify-between px-5 py-3"
          >
            <span className="text-[0.78rem] font-medium uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
              Advanced Options
            </span>
            <span className="text-[0.75rem] text-[var(--text-tertiary)]">
              {advancedOpen ? '▴ collapse' : '▾ expand'}
            </span>
          </button>

          {advancedOpen && (
            <div className="border-t border-white/6 px-5 pb-5 pt-4">
              <div className="grid gap-5 md:grid-cols-2">
                <StyledSelect
                  label="Auto Start"
                  options={[
                    { label: 'Yes', value: 'yes' },
                    { label: 'No', value: 'no' },
                  ]}
                  value={form.auto_start ? 'yes' : 'no'}
                  onChange={(v) => setForm((prev) => ({ ...prev, auto_start: v === 'yes' }))}
                />

                <DirectorySelect
                  color="amber"
                  directories={directories}
                  onRefresh={refreshDirectories}
                  isLoading={isRefreshingDirs}
                  value={form.output_dir}
                  base={form.base}
                  onChange={(path, base) =>
                    setForm((prev) => ({ ...prev, output_dir: path, base }))
                  }
                  showBaseLabel={showBaseLabel}
                />

                <div>
                  <label className="field-label">Subfolder</label>
                  <input
                    type="text"
                    value={form.sub_folder}
                    placeholder="e.g. music/albums"
                    onChange={(e) => setForm((prev) => ({ ...prev, sub_folder: e.target.value }))}
                    className="input-field input-amber"
                  />
                </div>

                <div>
                  <label className="field-label">Custom Name Prefix</label>
                  <input
                    type="text"
                    value={form.custom_prefix}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, custom_prefix: e.target.value }))
                    }
                    className="input-field input-amber"
                  />
                </div>

                <div>
                  <label className="field-label">Custom Output Filename</label>
                  <input
                    type="text"
                    value={form.custom_filename}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, custom_filename: e.target.value }))
                    }
                    className="input-field input-amber"
                  />
                </div>

                <div>
                  <label className="field-label">Playlist Item Limit</label>
                  <input
                    type="number"
                    min="0"
                    value={form.item_limit}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, item_limit: Number(e.target.value) || 0 }))
                    }
                    className="input-field input-amber"
                  />
                </div>

                <div className="flex items-end pb-1">
                  <ToggleSwitch
                    color="amber"
                    label="Split by chapters"
                    checked={form.split_chapters}
                    onChange={(checked) =>
                      setForm((prev) => ({ ...prev, split_chapters: checked }))
                    }
                  />
                </div>
              </div>

              {/* ── Cookies (inside advanced) ── */}
              <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-white/6 pt-4">
                <span className="text-[0.72rem] font-medium uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
                  Cookies
                </span>
                <label className="cursor-pointer rounded-lg border border-[var(--glass-border)] bg-[var(--bg-glass)] px-3 py-1.5 text-[0.8rem] text-[var(--text-secondary)] transition-all hover:border-[var(--glass-border-hover)] hover:text-[var(--text-primary)]">
                  Upload cookies.txt
                  <input
                    type="file"
                    className="hidden"
                    accept=".txt"
                    onChange={handleCookieUpload}
                  />
                </label>
                {status?.cookies_present && (
                  <button
                    type="button"
                    onClick={() => void handleCookieDelete()}
                    className="rounded-lg border border-red-500/20 px-3 py-1.5 text-[0.8rem] text-red-400 transition-all hover:bg-red-500/10"
                  >
                    Remove
                  </button>
                )}
                <span className="text-[0.75rem] text-[var(--text-tertiary)]">
                  {status?.cookies_present ? 'cookies.txt loaded' : 'No cookies configured'}
                </span>
              </div>
            </div>
          )}
        </div>

        {/* ── Active Downloads (always visible) ── */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-[0.92rem] font-semibold">Active Downloads</h3>
            <span className="text-[0.78rem] text-[var(--text-tertiary)]">
              {activeDownloads.length > 0 ? `${activeDownloads.length} active` : 'idle'}
            </span>
          </div>
          {activeDownloads.length > 0 ? (
            activeDownloads.map((job) => (
              <div key={job.job_id} className="glass-light rounded-[14px] p-4">
                <div className="mb-2 flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="truncate text-[0.88rem] font-medium text-[var(--text-primary)]">
                      {job.filename ?? job.url}
                    </p>
                    <p className="mt-0.5 text-[0.72rem] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
                      {job.status}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {job.status === 'queued' && (
                      <button
                        type="button"
                        onClick={() => handleStartQueuedJob(job.job_id)}
                        className="rounded-lg border border-[var(--accent-5)]/30 px-3 py-1 text-[0.72rem] font-medium text-[var(--accent-5)] transition-all hover:bg-[var(--accent-5)]/10"
                      >
                        Start
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void handleDeleteJob(job.job_id)}
                      className="rounded-lg border border-red-500/20 px-3 py-1 text-[0.72rem] font-medium text-red-400 transition-all hover:bg-red-500/10"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-white/6">
                  <div
                    className="h-full rounded-full bg-[var(--accent-5)] transition-all duration-300"
                    style={{
                      width: `${Math.max(0, Math.min(Number(job.progress) || 0, 100))}%`,
                    }}
                  />
                </div>
                <div className="mt-1.5 flex items-center gap-4 text-[0.72rem] text-[var(--text-secondary)]">
                  <span className="tabular-nums">{formatProgress(job.progress)}%</span>
                  {job.speed && <span>{job.speed}</span>}
                  <span className="ml-auto">{job.eta ? `ETA ${job.eta}` : 'Preparing...'}</span>
                </div>
                {job.error && <p className="mt-2 text-[0.78rem] text-red-400">{job.error}</p>}
              </div>
            ))
          ) : (
            <p className="py-3 text-center text-[0.8rem] text-[var(--text-tertiary)]">
              No active downloads
            </p>
          )}
        </div>

        {/* ── History ── */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h3 className="text-[0.92rem] font-semibold">History</h3>
              <span className="rounded bg-white/5 px-1.5 py-0.5 text-[0.62rem] font-medium uppercase tracking-wider text-[var(--text-tertiary)]">
                newest first
              </span>
            </div>
            <div className="flex items-center gap-3">
              {completedDownloads.some((j) => j.status === 'done') && (
                <button
                  type="button"
                  onClick={() => void handleClearCompleted()}
                  className="text-[0.72rem] text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-secondary)]"
                >
                  Clear completed
                </button>
              )}
              {completedDownloads.some((j) => j.status === 'error') && (
                <>
                  <button
                    type="button"
                    onClick={() => void handleClearFailed()}
                    className="text-[0.72rem] text-red-400/70 transition-colors hover:text-red-400"
                  >
                    Clear failed
                  </button>
                  <button
                    type="button"
                    onClick={handleRetryFailed}
                    className="text-[0.72rem] text-[var(--accent-5)] transition-colors hover:text-[var(--accent-5)]/80"
                  >
                    Retry failed
                  </button>
                </>
              )}
              <button
                type="button"
                onClick={() => void refreshJobs()}
                className={`rounded-lg border border-[var(--glass-border)] px-3 py-1 text-[0.72rem] text-[var(--text-secondary)] transition-all hover:border-[var(--glass-border-hover)] hover:text-[var(--text-primary)] ${isRefreshingJobs ? 'animate-pulse' : ''}`}
              >
                Refresh
              </button>
            </div>
          </div>

          {completedDownloads.length > 0 ? (
            <div className="space-y-2">
              {completedDownloads.map((job) => (
                <div
                  key={job.job_id}
                  className={`flex items-center justify-between gap-4 rounded-xl border p-4 ${
                    job.status === 'error'
                      ? 'border-red-500/15 bg-red-500/[0.03]'
                      : 'border-white/6 bg-white/[0.03]'
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className={job.status === 'done' ? 'text-green-400' : 'text-red-400'}>
                        {job.status === 'done' ? '✓' : '✗'}
                      </span>
                      <p className="truncate text-[0.88rem] font-medium text-[var(--text-primary)]">
                        {job.filename ?? job.url}
                      </p>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-3 pl-6 text-[0.72rem] text-[var(--text-tertiary)]">
                      {job.size && <span>{job.size}</span>}
                      <span>{new Date(job.created_at).toLocaleString()}</span>
                    </div>
                    {job.error && (
                      <p className="mt-1 pl-6 text-[0.75rem] text-red-400/80">{job.error}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {job.status === 'done' && (
                      <a
                        href={getDownloaderFileUrl(job.job_id)}
                        download
                        className="rounded-lg border border-[var(--accent-5)]/30 px-3 py-1 text-[0.72rem] font-medium text-[var(--accent-5)] transition-all hover:bg-[var(--accent-5)]/10"
                      >
                        Download
                      </a>
                    )}
                    {job.status === 'error' && (
                      <button
                        type="button"
                        onClick={() => void handleStartDownload(job.url)}
                        className="text-[0.72rem] text-[var(--accent-5)] transition-colors hover:text-[var(--accent-5)]/80"
                      >
                        Retry
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void handleDeleteJob(job.job_id)}
                      className="rounded-lg border border-white/8 px-3 py-1 text-[0.72rem] text-[var(--text-secondary)] transition-all hover:border-red-500/30 hover:text-red-400"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-4 text-center text-[0.82rem] text-[var(--text-tertiary)]">
              No recent downloads yet.
            </p>
          )}
        </div>

        {/* ── Status Footer ── */}
        <div className="flex items-center justify-center">
          <div className="inline-flex items-center gap-3 rounded-full border border-white/6 bg-white/[0.03] px-5 py-2 text-[0.72rem] text-[var(--text-tertiary)]">
            <span>yt-dlp {status?.yt_dlp_version ?? '...'}</span>
            <span className="text-white/10">·</span>
            <span>Cookies: {status?.cookies_present ? 'loaded' : 'none'}</span>
          </div>
        </div>
      </div>

      {/* ── Bulk Modal ── */}
      {isBulkModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-6">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setIsBulkModalOpen(false)}
          />
          <div
            className="glass-strong relative w-full max-w-[520px] p-6"
            role="dialog"
            aria-modal="true"
            aria-labelledby="bulk-url-import-title"
          >
            <h3 id="bulk-url-import-title" className="mb-4 text-[1.1rem] font-semibold">
              Bulk URL Import
            </h3>
            <textarea
              value={bulkUrls}
              onChange={(e) => setBulkUrls(e.target.value)}
              className="min-h-[220px] w-full rounded-[12px] border border-[var(--glass-border)] bg-[var(--bg-input)] p-4 text-[0.9rem] text-[var(--text-primary)] outline-none transition-all focus:border-[var(--accent-5)] focus:shadow-[0_0_0_3px_var(--accent-5-glow)]"
              placeholder="Paste one URL per line..."
            />
            <div className="mt-4 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setIsBulkModalOpen(false)}
                className="rounded-lg px-4 py-2 text-[0.9rem] font-medium text-[var(--text-tertiary)] transition-all hover:text-[var(--text-primary)]"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleBulkSubmit}
                className="btn-submit btn-amber h-[40px] w-auto px-6"
              >
                Add All
              </button>
            </div>
          </div>
        </div>
      )}
    </PanelLayout>
  )
}
