import { useCallback, useEffect, useMemo, useState } from 'react'
import PanelLayout from './PanelLayout'
import DownloadJobCard from './downloader/DownloadJobCard'
import DownloadOptions from './downloader/DownloadOptions'
import FormSection from './ui/FormSection'
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
      <div>
        <FormSection label="Add downloads">
          <div className="space-y-3">
            <textarea
              value={form.url}
              placeholder="Paste a link — or several, one per line"
              rows={urls.length > 1 ? Math.min(urls.length + 1, 8) : 2}
              onChange={(e) => patchForm({ url: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && urls.length <= 1) {
                  e.preventDefault()
                  void submit()
                }
              }}
              className="input-field input-cyan min-h-[76px] resize-y leading-relaxed"
            />
            <button type="button" onClick={() => void submit()} className="btn-submit btn-cyan">
              {urls.length > 1 ? `Download ${urls.length} links` : 'Download'}
            </button>
          </div>
        </FormSection>

        {(localError || error) && (
          <div
            className="mb-6 flex items-center justify-between rounded-lg border border-red-500/15 bg-red-500/[0.06] px-4 py-2.5"
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
        />

        <FormSection label="Cookies">
          <div className="flex flex-wrap items-center gap-3">
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
        </FormSection>

        <FormSection label={active.length > 0 ? `Active — ${active.length}` : 'Active'}>
          <div className="space-y-3">
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
              <p className="rounded-[14px] border border-white/6 bg-white/[0.02] py-5 text-center text-[0.8rem] text-[var(--text-tertiary)]">
                Nothing downloading right now.
              </p>
            )}
          </div>
        </FormSection>

        <FormSection label="History">
          <div className="space-y-3">
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
              <p className="rounded-[14px] border border-white/6 bg-white/[0.02] py-5 text-center text-[0.82rem] text-[var(--text-tertiary)]">
                Finished downloads will be listed here.
              </p>
            )}
          </div>
        </FormSection>

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
