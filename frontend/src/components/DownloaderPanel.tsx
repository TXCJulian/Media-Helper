import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import PanelLayout from './PanelLayout'
import DirectorySelect from './ui/DirectorySelect'
import ToggleSwitch from './ui/ToggleSwitch'
import {
  deleteCookies,
  deleteDownloadJob,
  fetchDownloadJobs,
  fetchDownloaderStatus,
  fetchMediaDirectories,
  postCookies,
  postDownload,
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
    progress: patch.progress ?? 0,
    speed: patch.speed ?? null,
    eta: patch.eta ?? null,
    filename: patch.filename ?? null,
    error: patch.error ?? null,
    created_at: patch.created_at ?? new Date().toISOString(),
    size: patch.size ?? null,
  }

  const idx = prevJobs.findIndex((job) => job.job_id === patch.job_id)
  if (idx === -1) return [nextJob, ...prevJobs]

  const merged: DownloadJob = {
    ...prevJobs[idx],
    ...patch,
    speed: patch.speed ?? prevJobs[idx]!.speed,
    eta: patch.eta ?? prevJobs[idx]!.eta,
    filename: patch.filename ?? prevJobs[idx]!.filename,
    error: patch.error ?? prevJobs[idx]!.error,
    size: patch.size ?? prevJobs[idx]!.size,
    job_id: patch.job_id,
    url: patch.url ?? prevJobs[idx]!.url,
    status: patch.status ?? prevJobs[idx]!.status,
    progress: patch.progress ?? prevJobs[idx]!.progress,
    created_at: patch.created_at ?? prevJobs[idx]!.created_at,
  }

  return prevJobs.map((job, jobIdx) => (jobIdx === idx ? merged : job))
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

  // Persist settings on every form change (except url)
  useEffect(() => {
    saveSettings(form)
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
      if (patch.url && patch.status) {
        onLog([...logRef.current, `${patch.status}: ${patch.filename ?? patch.url}`])
      }
    },
    [onLog],
  )

  const handleStartDownload = async (urlOverride?: string) => {
    const url = (urlOverride ?? form.url).trim()
    if (!url) {
      setLocalError('Please enter a URL')
      return
    }

    setLocalError('')
    onLog([...logRef.current, `queued: ${url}`])

    postDownload(
      { ...form, url },
      {
        onProgress: (data) => applyEventPatch(data),
        onError: (data) => {
          const patch = parseDownloadEvent(data)
          if (patch) {
            setJobs((prev) => mergeJob(prev, patch))
            if (patch.error) onError(patch.error)
            return
          }
          onError(data)
        },
        onDone: (data) => {
          applyEventPatch(data)
          refreshJobs().catch(() => {})
        },
      },
    )

    if (!urlOverride) setForm((prev) => ({ ...prev, url: '' }))
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

  const dropdownClass = 'input-field input-amber h-[38px] min-w-0 text-[0.82rem]'

  return (
    <PanelLayout title="Downloader" onBack={onBack} maxWidth="920px">
      <div className="space-y-6">
        {/* ── URL Hero ── */}
        <div className="flex flex-col gap-3 md:flex-row">
          <input
            type="text"
            value={form.url}
            onChange={(e) => setForm((prev) => ({ ...prev, url: e.target.value }))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleStartDownload()
            }}
            className="input-field input-amber flex-1"
          />
          <button
            type="button"
            onClick={() => void handleStartDownload()}
            className="btn-submit btn-amber md:w-[180px]"
          >
            Download
          </button>
          <button
            type="button"
            onClick={() => setIsBulkModalOpen(true)}
            className="rounded-[12px] border border-[var(--glass-border)] bg-[var(--bg-glass)] px-5 py-2.5 text-[0.88rem] font-medium text-[var(--text-secondary)] transition-all hover:border-[var(--glass-border-hover)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] md:w-[140px]"
          >
            Bulk Add
          </button>
        </div>

        {(localError || error) && (
          <p className="text-[0.8rem] text-red-400">{localError || error}</p>
        )}

        {/* ── Quick Settings Row ── */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[110px]">
            <label className="field-label">Type</label>
            <select
              value={form.type}
              onChange={(e) => handleTypeChange(e.target.value)}
              className={dropdownClass}
            >
              <option value="video">Video</option>
              <option value="audio">Audio</option>
              <option value="thumbnail">Thumbnail</option>
            </select>
          </div>

          {form.type !== 'thumbnail' && (
            <div className="min-w-[110px]">
              <label className="field-label">Codec</label>
              <select
                value={form.codec}
                onChange={(e) => setForm((prev) => ({ ...prev, codec: e.target.value }))}
                className={dropdownClass}
              >
                {(CODEC_OPTIONS[form.type] ?? []).map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="min-w-[110px]">
            <label className="field-label">Format</label>
            <select
              value={form.format}
              onChange={(e) => setForm((prev) => ({ ...prev, format: e.target.value }))}
              className={dropdownClass}
            >
              {(FORMAT_OPTIONS[form.type] ?? []).map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {form.type !== 'thumbnail' && (
            <div className="min-w-[110px]">
              <label className="field-label">Quality</label>
              <select
                value={form.quality}
                onChange={(e) => setForm((prev) => ({ ...prev, quality: e.target.value }))}
                className={dropdownClass}
              >
                {qualityOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
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
                <div>
                  <label className="field-label">Auto Start</label>
                  <select
                    value={form.auto_start ? 'yes' : 'no'}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, auto_start: e.target.value === 'yes' }))
                    }
                    className={dropdownClass}
                  >
                    <option value="yes">Yes</option>
                    <option value="no">No</option>
                  </select>
                </div>

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
            </div>
          )}
        </div>

        {/* ── Cookies + Bulk Actions Footer ── */}
        <div className="flex flex-wrap items-center justify-between gap-4 rounded-[14px] border border-white/6 bg-white/[0.02] px-5 py-3">
          <div className="flex items-center gap-3">
            <span className="text-[0.72rem] font-medium uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
              Cookies
            </span>
            <label className="cursor-pointer rounded-lg border border-[var(--glass-border)] bg-[var(--bg-glass)] px-3 py-1.5 text-[0.8rem] text-[var(--text-secondary)] transition-all hover:border-[var(--glass-border-hover)] hover:text-[var(--text-primary)]">
              Upload cookies.txt
              <input type="file" className="hidden" accept=".txt" onChange={handleCookieUpload} />
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

          <div className="flex items-center gap-2">
            <span className="text-[0.72rem] font-medium uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
              Bulk
            </span>
            <button
              type="button"
              onClick={() => setIsBulkModalOpen(true)}
              className="rounded-lg border border-[var(--glass-border)] bg-[var(--bg-glass)] px-3 py-1.5 text-[0.8rem] text-[var(--text-secondary)] transition-all hover:border-[var(--glass-border-hover)] hover:text-[var(--text-primary)]"
            >
              Import URLs
            </button>
          </div>
        </div>

        {/* ── Active Downloads ── */}
        {activeDownloads.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-[0.92rem] font-semibold">Active Downloads</h3>
              <span className="text-[0.78rem] text-[var(--text-tertiary)]">
                {activeDownloads.length} active
              </span>
            </div>
            {activeDownloads.map((job) => (
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
                  <button
                    type="button"
                    onClick={() => void handleDeleteJob(job.job_id)}
                    className="rounded-lg border border-red-500/20 px-3 py-1 text-[0.72rem] font-medium text-red-400 transition-all hover:bg-red-500/10"
                  >
                    Cancel
                  </button>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-white/6">
                  <div
                    className="h-full rounded-full bg-[var(--accent-5)] transition-all duration-300"
                    style={{ width: `${Math.max(0, Math.min(job.progress, 100))}%` }}
                  />
                </div>
                <div className="mt-1.5 flex flex-wrap items-center justify-between gap-3 text-[0.72rem] text-[var(--text-secondary)]">
                  <span>{job.progress.toFixed(1)}%</span>
                  <span>{job.speed ?? '...'}</span>
                  <span>{job.eta ? `ETA ${job.eta}` : 'Preparing...'}</span>
                </div>
                {job.error && <p className="mt-2 text-[0.78rem] text-red-400">{job.error}</p>}
              </div>
            ))}
          </div>
        )}

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
        <div className="flex items-center gap-4 text-[0.72rem] text-[var(--text-tertiary)]">
          <span>yt-dlp {status?.yt_dlp_version ?? '...'}</span>
          <span>·</span>
          <span>Cookies: {status?.cookies_present ? 'loaded' : 'none'}</span>
        </div>
      </div>

      {/* ── Bulk Modal ── */}
      {isBulkModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-6">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setIsBulkModalOpen(false)}
          />
          <div className="glass-strong relative w-full max-w-[520px] p-6">
            <h3 className="mb-4 text-[1.1rem] font-semibold">Bulk URL Import</h3>
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
