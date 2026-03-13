import { useState, useEffect, useCallback } from 'react'
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
  ready: 'bg-white/10 text-white/50',
  cutting: 'bg-amber-400/15 text-amber-300',
  done: 'bg-emerald-400/15 text-emerald-300',
  error: 'bg-red-400/15 text-red-300',
}

export default function JobManager({ activeJobId, onLog }: { activeJobId?: string; onLog?: (msg: string) => void }) {
  const [jobs, setJobs] = useState<CutterJob[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [savingFile, setSavingFile] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listJobs()
      setJobs(data.jobs ?? [])
    } catch {
      // silently fail
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
        className={`flex w-full items-center gap-2 border border-[var(--glass-border)] bg-[var(--glass-bg)] px-4 py-2.5 text-left text-[0.8rem] font-medium text-white/70 backdrop-blur-sm transition hover:border-emerald-400/30 hover:text-white/90 ${open ? 'rounded-t-xl' : 'rounded-xl'}`}
      >
        <span className={`inline-block transition-transform ${open ? 'rotate-90' : ''}`}>&#9654;</span>
        Jobs {jobs.length > 0 && `(${jobs.length})`}
        {loading && <span className="spinner-sm ml-auto" />}
        {open && !loading && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); void refresh() }}
            className="ml-auto flex h-[26px] w-[26px] cursor-pointer items-center justify-center rounded-[7px] border border-[var(--border)] bg-[var(--bg-input)] text-[0.8rem] text-[var(--text-secondary)] transition-all duration-200 hover:border-[var(--glass-border-hover)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
            title="Refresh jobs"
          >
            ↻
          </button>
        )}
      </button>

      {open && (
        <div className="max-h-[320px] overflow-y-auto rounded-b-xl border border-t-0 border-[var(--glass-border)] bg-black/20">
          {jobs.length === 0 ? (
            <div className="px-4 py-6 text-center text-[0.78rem] text-white/30">
              No jobs found
            </div>
          ) : (
            jobs.map((job) => (
              <div
                key={job.job_id}
                className="flex items-start gap-3 border-b border-white/5 px-4 py-3 last:border-b-0"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[0.78rem] text-white/80">
                      {job.original_name}
                    </span>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase ${STATUS_COLORS[job.status] ?? STATUS_COLORS.ready}`}>
                      {job.status}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-[0.68rem] text-white/35">
                    <span>{job.source}</span>
                    <span>{relativeTime(job.created_at)}</span>
                  </div>
                  {job.output_files.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
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
                                  .catch((err) => onLog?.(`Save failed: ${err instanceof Error ? err.message : String(err)}`))
                                  .finally(() => setSavingFile(null))
                              }}
                              className="inline-flex items-center gap-0.5 rounded border border-emerald-400/15 bg-emerald-400/5 px-1.5 py-0.5 text-[0.58rem] text-emerald-400/60 transition hover:border-emerald-400/30 hover:text-emerald-400/90"
                              title="Save to original file directory"
                            >
                              {savingFile === `${job.job_id}:${file}` ? (
                                <span className="spinner-xs" />
                              ) : (
                                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" />
                                  <polyline points="17 21 17 13 7 13 7 21" />
                                  <polyline points="7 3 7 8 15 8" />
                                </svg>
                              )}
                              Save
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => void handleDelete(job.job_id)}
                  disabled={job.job_id === activeJobId}
                  className={`shrink-0 rounded-md p-1.5 transition ${
                    job.job_id === activeJobId
                      ? 'cursor-not-allowed text-white/10'
                      : 'text-white/25 hover:bg-red-500/10 hover:text-red-400'
                  }`}
                  title={job.job_id === activeJobId ? 'Cannot delete active job' : 'Delete job'}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6" />
                    <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" />
                    <path d="M10 11v6" />
                    <path d="M14 11v6" />
                    <path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2" />
                  </svg>
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
