import { useState, useEffect, useCallback, useRef } from 'react'
import IconButton from '@/components/ui/IconButton'
import StatusPill from '@/components/ui/StatusPill'
import { PencilIcon, SaveIcon, TrashIcon } from '@/components/ui/icons'
import { listJobs, deleteJob, getDownloadUrl, saveToSource } from '@/lib/api'
import type { CutterJob } from '@/types'

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

const STATUS_COLORS: Record<string, string> = {
  uploading: 'bg-cyan-400/15 text-cyan-300',
  ready: 'bg-white/10 text-white/50',
  cutting: 'bg-amber-400/15 text-amber-300',
  full_transcoding: 'bg-blue-400/15 text-blue-300',
  audio_transcoding: 'bg-blue-400/15 text-blue-300',
  transcoding: 'bg-blue-400/15 text-blue-300',
  done: 'bg-emerald-400/15 text-emerald-300',
  error: 'bg-red-400/15 text-red-300',
}

export default function JobManager({
  activeJobId,
  onLog,
  onOpenJob,
  showBaseLabel,
}: {
  activeJobId?: string
  onLog?: (msg: string) => void
  onOpenJob?: (job: CutterJob) => void
  showBaseLabel?: boolean
}) {
  const [jobs, setJobs] = useState<CutterJob[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [savingFile, setSavingFile] = useState<string | null>(null)
  const [refreshError, setRefreshError] = useState<string>('')
  const onLogRef = useRef(onLog)

  useEffect(() => {
    onLogRef.current = onLog
  }, [onLog])

  const refresh = useCallback(async () => {
    setLoading(true)
    setRefreshError('')
    try {
      const data = await listJobs()
      setJobs(data.jobs ?? [])
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setRefreshError(message)
      onLogRef.current?.(`Failed to refresh jobs: ${message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void refresh()
  }, [open, refresh])

  const handleDelete = async (jobId: string) => {
    try {
      await deleteJob(jobId)
      setJobs((prev) => prev.filter((j) => j.job_id !== jobId))
    } catch (err) {
      onLog?.(`Failed to delete job: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  return (
    <div className="mt-6">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`flex w-full cursor-pointer items-center gap-2 border border-[var(--glass-border)] bg-[var(--glass-bg)] px-4 py-2.5 text-left text-[0.8rem] font-medium text-white/70 backdrop-blur-sm transition hover:border-emerald-400/30 hover:text-white/90 ${open ? 'rounded-t-xl' : 'rounded-xl'}`}
      >
        <span className={`inline-block transition-transform ${open ? 'rotate-90' : ''}`}>
          &#9654;
        </span>
        Jobs {jobs.length > 0 && `(${jobs.length})`}
        {open && (
          <span className="ml-auto flex h-[26px] w-[26px] items-center justify-center">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                if (!loading) void refresh()
              }}
              disabled={loading}
              className="flex h-[26px] w-[26px] cursor-pointer items-center justify-center rounded-[7px] border border-[var(--border)] bg-[var(--bg-input)] text-[0.8rem] text-[var(--text-secondary)] transition-all duration-200 hover:border-[var(--glass-border-hover)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] disabled:cursor-wait disabled:opacity-80"
              title={loading ? 'Refreshing jobs...' : 'Refresh jobs'}
            >
              {loading ? <span className="spinner-xs" /> : '\u21BB'}
            </button>
          </span>
        )}
      </button>

      {open && (
        <div className="max-h-[min(480px,60vh)] space-y-3 overflow-y-auto rounded-b-xl border border-t-0 border-[var(--glass-border)] bg-black/20 p-4">
          {refreshError && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2 text-[0.72rem] text-red-300">
              Failed to refresh jobs: {refreshError}
            </div>
          )}
          {jobs.length === 0 ? (
            <p className="rounded-[14px] border border-white/6 bg-white/[0.02] py-5 text-center text-[0.8rem] text-[var(--text-tertiary)]">
              No jobs found
            </p>
          ) : (
            jobs.map((job) => (
              <div key={job.job_id} className="glass-light rounded-[14px] p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[0.88rem] font-medium text-[var(--text-primary)]">
                      {job.original_name}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <StatusPill className={STATUS_COLORS[job.status] ?? STATUS_COLORS.ready}>
                        {job.status.replace(/_/g, ' ')}
                      </StatusPill>
                      {job.status === 'ready' && job.browser_ready && (
                        <StatusPill
                          className="bg-emerald-400/15 text-emerald-300"
                          title="File is already browser-compatible"
                        >
                          browser ready
                        </StatusPill>
                      )}
                      {job.status === 'ready' && !job.browser_ready && job.preview_transcoded && (
                        <StatusPill
                          className="bg-sky-400/15 text-sky-300"
                          title="Transcoded preview exists on disk; player may still be preparing the stream"
                        >
                          preview cached
                        </StatusPill>
                      )}
                      {job.audio_transcoded_tracks && job.audio_transcoded_tracks.length > 0 && (
                        <StatusPill
                          className="bg-violet-400/15 text-violet-300"
                          title={`Audio tracks transcoded: ${job.audio_transcoded_tracks.join(', ')}`}
                        >
                          audio cached
                        </StatusPill>
                      )}
                      {job.transcode_error && (
                        <StatusPill
                          className="cursor-help bg-red-400/15 text-red-300"
                          title={job.transcode_error}
                        >
                          transcode err
                        </StatusPill>
                      )}
                    </div>
                    <div className="mt-1.5 flex items-center gap-3 text-[0.72rem] text-[var(--text-secondary)]">
                      <span>{job.source}</span>
                      {showBaseLabel && job.base && (
                        <span className="text-[var(--text-tertiary)]">{job.base}</span>
                      )}
                      <span className="text-[var(--text-tertiary)]">
                        {relativeTime(job.created_at)}
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {onOpenJob && (
                      <IconButton
                        label={
                          job.job_id === activeJobId ? 'Currently active job' : 'Open job settings'
                        }
                        onClick={() => {
                          onOpenJob(job)
                          setOpen(false)
                        }}
                        disabled={job.job_id === activeJobId}
                        tone="accent"
                        accentClass="hover:bg-emerald-500/10 hover:text-emerald-400"
                      >
                        <PencilIcon />
                      </IconButton>
                    )}
                    <IconButton
                      label={job.job_id === activeJobId ? 'Cannot delete active job' : 'Delete job'}
                      onClick={() => void handleDelete(job.job_id)}
                      disabled={job.job_id === activeJobId}
                      tone="danger"
                    >
                      <TrashIcon />
                    </IconButton>
                  </div>
                </div>

                {job.output_files.length > 0 && (
                  <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-white/6 pt-3">
                    {job.output_files.map((file) => (
                      <div key={file} className="inline-flex items-center gap-1.5">
                        <a
                          href={getDownloadUrl(job.job_id, file)}
                          download
                          className="font-mono text-[0.68rem] text-emerald-400/70 underline decoration-emerald-400/20 hover:decoration-emerald-400/60"
                        >
                          &darr; {file}
                        </a>
                        {job.source === 'server' && (
                          <button
                            type="button"
                            disabled={savingFile === `${job.job_id}:${file}`}
                            onClick={() => {
                              const key = `${job.job_id}:${file}`
                              setSavingFile(key)
                              saveToSource(job.job_id, file)
                                .then(() => onLog?.(`Saved ${file} to source directory`))
                                .catch((err) =>
                                  onLog?.(
                                    `Save failed: ${err instanceof Error ? err.message : String(err)}`,
                                  ),
                                )
                                .finally(() => setSavingFile(null))
                            }}
                            className="inline-flex items-center gap-0.5 rounded border border-emerald-400/15 bg-emerald-400/5 px-1.5 py-0.5 text-[0.58rem] text-emerald-400/60 transition hover:border-emerald-400/30 hover:text-emerald-400/90"
                            title="Save to original file directory"
                          >
                            {savingFile === `${job.job_id}:${file}` ? (
                              <span className="spinner-xs" />
                            ) : (
                              <SaveIcon size={10} />
                            )}
                            Save
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
