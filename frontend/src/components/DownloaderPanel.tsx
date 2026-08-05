import { useCallback, useEffect, useMemo, useState } from 'react'
import PanelLayout from './PanelLayout'
import DownloadJobCard from './downloader/DownloadJobCard'
import DownloadOptions from './downloader/DownloadOptions'
import { useDownloadStream } from '@/hooks/useDownloadStream'
import {
  cancelDownloadJob,
  createDownloads,
  deleteCookies,
  deleteDownloadJob,
  fetchDownloaderStatus,
  fetchMediaDirectories,
  postCookies,
  startDownloadJob,
} from '@/lib/api'
import type { DirectoryEntry, DownloadForm, DownloaderStatus } from '@/types'

const STORAGE_KEY = 'downloader-settings'

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
}

function loadSettings(): Omit<DownloadForm, 'url'> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? { ...DEFAULT_FORM, ...JSON.parse(raw) } : DEFAULT_FORM
  } catch {
    return DEFAULT_FORM
  }
}

/** Split pasted input into URLs — one per line, blanks ignored. */
export function parseUrls(input: string): string[] {
  return input
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

interface DownloaderPanelProps {
  onError: (err: string) => void
  onBack: () => void
  error: string
  showBaseLabel?: boolean
}

export default function DownloaderPanel({
  onError,
  onBack,
  error,
  showBaseLabel,
}: DownloaderPanelProps) {
  const { jobs, connected } = useDownloadStream()
  const [status, setStatus] = useState<DownloaderStatus | null>(null)
  const [directories, setDirectories] = useState<DirectoryEntry[]>([])
  const [isRefreshingDirs, setIsRefreshingDirs] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [localError, setLocalError] = useState('')
  const [form, setForm] = useState<DownloadForm>(() => ({ url: '', ...loadSettings() }))

  const urls = useMemo(() => parseUrls(form.url), [form.url])

  useEffect(() => {
    const id = window.setTimeout(() => {
      const { url: _ignored, ...settings } = form
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
    }, 300)
    return () => window.clearTimeout(id)
  }, [form])

  const refreshStatus = useCallback(async () => {
    setStatus(await fetchDownloaderStatus())
  }, [])

  const refreshDirectories = useCallback(async () => {
    setIsRefreshingDirs(true)
    try {
      setDirectories((await fetchMediaDirectories()).directories)
    } finally {
      setIsRefreshingDirs(false)
    }
  }, [])

  useEffect(() => {
    void refreshStatus().catch(() => {})
    void refreshDirectories().catch(() => {})
  }, [refreshDirectories, refreshStatus])

  const patchForm = useCallback((patch: Partial<DownloadForm>) => {
    setForm((prev) => ({ ...prev, ...patch }))
  }, [])

  const submit = async (override?: string[]) => {
    const targets = override ?? urls
    if (targets.length === 0) {
      setLocalError('Please enter at least one URL')
      return
    }
    setLocalError('')
    onError('')
    const { url: _ignored, ...options } = form
    try {
      await createDownloads(targets, options)
      if (!override) setForm((prev) => ({ ...prev, url: '' }))
      await refreshStatus()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to create download')
    }
  }

  const guard = (action: Promise<unknown>, message: string) =>
    action.catch((err) => onError(err instanceof Error ? err.message : message))

  const active = jobs.filter((j) => ['queued', 'downloading', 'transcoding'].includes(j.stage))
  const history = jobs.filter((j) => ['done', 'error', 'cancelled'].includes(j.stage))

  return (
    <PanelLayout title="Downloader" onBack={onBack} maxWidth="920px">
      <div className="space-y-6">
        <div className="flex flex-col gap-3">
          <textarea
            value={form.url}
            placeholder="Paste a URL — or several, one per line"
            rows={urls.length > 1 ? Math.min(urls.length + 1, 8) : 1}
            onChange={(e) => patchForm({ url: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && urls.length <= 1) {
                e.preventDefault()
                void submit()
              }
            }}
            className="input-field input-cyan resize-y"
          />
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void submit()}
              className="btn-submit btn-cyan h-[42px] !w-auto px-5 text-[0.85rem]"
            >
              {urls.length > 1 ? `Download ${urls.length} URLs` : 'Download'}
            </button>
            {urls.length > 1 && (
              <span className="text-[0.78rem] text-[var(--text-tertiary)]">
                {urls.length} URLs detected
              </span>
            )}
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

        <DownloadOptions
          form={form}
          onChange={patchForm}
          directories={directories}
          onRefreshDirectories={() => void refreshDirectories()}
          isRefreshingDirectories={isRefreshingDirs}
          showBaseLabel={showBaseLabel}
          advancedOpen={advancedOpen}
          onToggleAdvanced={() => setAdvancedOpen((v) => !v)}
        />

        <div className="flex flex-wrap items-center gap-3 rounded-[14px] border border-white/6 bg-white/[0.02] px-5 py-3">
          <span className="text-[0.72rem] font-medium uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
            Cookies
          </span>
          <label className="cursor-pointer rounded-lg border border-[var(--glass-border)] bg-[var(--bg-glass)] px-3 py-1.5 text-[0.8rem] text-[var(--text-secondary)] transition-all hover:border-[var(--glass-border-hover)] hover:text-[var(--text-primary)]">
            Upload cookies.txt
            <input
              type="file"
              className="hidden"
              accept=".txt"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) {
                  void guard(postCookies(file).then(refreshStatus), 'Failed to upload cookies')
                }
                e.target.value = ''
              }}
            />
          </label>
          {status?.cookies_present && (
            <button
              type="button"
              onClick={() =>
                void guard(deleteCookies().then(refreshStatus), 'Failed to delete cookies')
              }
              className="rounded-lg border border-red-500/20 px-3 py-1.5 text-[0.8rem] text-red-400 transition-all hover:bg-red-500/10"
            >
              Remove
            </button>
          )}
          <span className="text-[0.75rem] text-[var(--text-tertiary)]">
            {status?.cookies_present ? 'cookies.txt loaded' : 'No cookies configured'}
          </span>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-[0.92rem] font-semibold">Active Downloads</h3>
            <span className="text-[0.78rem] text-[var(--text-tertiary)]">
              {active.length > 0 ? `${active.length} active` : 'idle'}
            </span>
          </div>
          {active.length > 0 ? (
            active.map((job) => (
              <DownloadJobCard
                key={job.job_id}
                job={job}
                onCancel={(id) => void guard(cancelDownloadJob(id), 'Failed to cancel')}
                onDelete={(id) => void guard(deleteDownloadJob(id), 'Failed to delete')}
                onStart={(id) => void guard(startDownloadJob(id), 'Failed to start')}
                onRetry={(url) => void submit([url])}
              />
            ))
          ) : (
            <p className="py-3 text-center text-[0.8rem] text-[var(--text-tertiary)]">
              No active downloads
            </p>
          )}
        </div>

        <div className="space-y-3">
          <h3 className="text-[0.92rem] font-semibold">History</h3>
          {history.length > 0 ? (
            history.map((job) => (
              <DownloadJobCard
                key={job.job_id}
                job={job}
                onCancel={(id) => void guard(cancelDownloadJob(id), 'Failed to cancel')}
                onDelete={(id) => void guard(deleteDownloadJob(id), 'Failed to delete')}
                onStart={(id) => void guard(startDownloadJob(id), 'Failed to start')}
                onRetry={(url) => void submit([url])}
              />
            ))
          ) : (
            <p className="py-4 text-center text-[0.82rem] text-[var(--text-tertiary)]">
              No recent downloads yet.
            </p>
          )}
        </div>

        <div className="flex items-center justify-center">
          <div className="inline-flex items-center gap-3 rounded-full border border-white/6 bg-white/[0.03] px-5 py-2 text-[0.72rem] text-[var(--text-tertiary)]">
            <span>yt-dlp {status?.yt_dlp_version ?? '...'}</span>
            <span className="text-white/10">·</span>
            <span>{connected ? 'Live' : 'Reconnecting...'}</span>
            <span className="text-white/10">·</span>
            <span>Queue: {status?.queue_depth ?? 0}</span>
          </div>
        </div>
      </div>
    </PanelLayout>
  )
}
