import { getDownloadItemFileUrl } from '@/lib/api'
import type { DownloadJob, DownloadStage } from '@/types'

const STAGE_LABELS: Record<DownloadStage, string> = {
  queued: 'Queued',
  downloading: 'Downloading',
  transcoding: 'Transcoding',
  done: 'Done',
  cancelled: 'Cancelled',
  error: 'Failed',
}

const PIPELINE: DownloadStage[] = ['downloading', 'transcoding', 'done']

function formatSize(bytes: number | null): string {
  if (bytes === null) return ''
  const units = ['B', 'KiB', 'MiB', 'GiB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`
}

/**
 * A per-item message from the backend.
 *
 * `item.error` carries two different things: an actual failure on an item
 * whose stage is `error`, and an advisory note on an item that succeeded —
 * the pre-existing-file explanation, which is the entire mitigation for a
 * known spec deviation and is useless if it is never shown. Only the first
 * is styled as a failure.
 */
function ItemMessage({ stage, error }: { stage: DownloadStage; error: string }) {
  const failed = stage === 'error'
  return (
    <p
      data-testid="item-error"
      className={`mt-1 text-[0.72rem] ${failed ? 'text-red-400' : 'text-amber-400/80'}`}
    >
      {error}
    </p>
  )
}

interface Props {
  job: DownloadJob
  onCancel: (jobId: string) => void
  onDelete: (jobId: string) => void
  onStart: (jobId: string) => void
  onRetry: (url: string) => void
}

export default function DownloadJobCard({ job, onCancel, onDelete, onStart, onRetry }: Props) {
  const isActive = ['queued', 'downloading', 'transcoding'].includes(job.stage)
  const stages = job.has_transcode ? PIPELINE : PIPELINE.filter((s) => s !== 'transcoding')
  const reached = (stage: DownloadStage) =>
    job.stage === 'done' || stages.indexOf(job.stage) >= stages.indexOf(stage)

  const overall =
    job.items.length > 0
      ? job.items.reduce((sum, item) => sum + item.progress, 0) / job.items.length
      : 0

  return (
    <div className="glass-light rounded-[14px] p-4">
      <div className="mb-2 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate text-[0.88rem] font-medium text-[var(--text-primary)]">
            {job.items[0]?.title || job.url}
          </p>
          <div className="mt-1 flex items-center gap-2">
            {stages.map((stage) => (
              <span
                key={stage}
                className={`text-[0.68rem] uppercase tracking-[0.1em] ${
                  job.stage === stage
                    ? 'text-[var(--accent-6)]'
                    : reached(stage)
                      ? 'text-[var(--text-secondary)]'
                      : 'text-[var(--text-tertiary)]/40'
                }`}
              >
                {STAGE_LABELS[stage]}
              </span>
            ))}
            {!isActive && job.stage !== 'done' && (
              <span className="text-[0.68rem] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
                {STAGE_LABELS[job.stage]}
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {job.stage === 'queued' && (
            <button
              type="button"
              onClick={() => onStart(job.job_id)}
              className="rounded-lg border border-[var(--accent-6)]/30 px-3 py-1 text-[0.72rem] font-medium text-[var(--accent-6)] transition-all hover:bg-[var(--accent-6)]/10"
            >
              Start
            </button>
          )}
          {isActive && (
            <button
              type="button"
              onClick={() => onCancel(job.job_id)}
              className="rounded-lg border border-white/8 px-3 py-1 text-[0.72rem] text-[var(--text-secondary)] transition-all hover:border-red-500/30 hover:text-red-400"
            >
              Cancel
            </button>
          )}
          {(job.stage === 'error' || job.stage === 'cancelled') && (
            <button
              type="button"
              onClick={() => onRetry(job.url)}
              className="rounded-lg border border-[var(--accent-6)]/30 px-3 py-1 text-[0.72rem] text-[var(--accent-6)] transition-all hover:bg-[var(--accent-6)]/10"
            >
              Retry
            </button>
          )}
          <button
            type="button"
            onClick={() => onDelete(job.job_id)}
            className="rounded-lg border border-white/8 px-3 py-1 text-[0.72rem] text-[var(--text-secondary)] transition-all hover:border-red-500/30 hover:text-red-400"
          >
            Delete
          </button>
        </div>
      </div>

      {isActive && (
        <>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/6">
            <div
              className="h-full rounded-full bg-[var(--accent-6)] transition-all duration-300"
              style={{ width: `${Math.max(0, Math.min(overall, 100))}%` }}
            />
          </div>
          <p className="mt-1.5 text-[0.72rem] tabular-nums text-[var(--text-secondary)]">
            {overall.toFixed(1)}%
          </p>
        </>
      )}

      {job.items.length > 1 && (
        <ul className="mt-3 space-y-1.5 border-t border-white/6 pt-3">
          {job.items.map((item) => (
            <li key={item.index} className="text-[0.75rem]">
              <div className="flex items-center gap-3">
                <span className="min-w-0 flex-1 truncate text-[var(--text-secondary)]">
                  {item.title}
                </span>
                <span className="tabular-nums text-[var(--text-tertiary)]">
                  {item.stage === 'done' ? formatSize(item.size) : `${item.progress.toFixed(0)}%`}
                </span>
                {item.stage === 'done' && (
                  <a
                    href={getDownloadItemFileUrl(job.job_id, item.index)}
                    download
                    className="text-[var(--accent-6)]"
                  >
                    Save
                  </a>
                )}
              </div>
              {item.error && <ItemMessage stage={item.stage} error={item.error} />}
            </li>
          ))}
        </ul>
      )}

      {job.items.length === 1 && (
        <>
          {job.stage === 'done' && (
            <div className="mt-2 flex items-center gap-3 text-[0.72rem] text-[var(--text-tertiary)]">
              <span>{formatSize(job.items[0]!.size)}</span>
              <a
                href={getDownloadItemFileUrl(job.job_id, 0)}
                download
                className="text-[var(--accent-6)]"
              >
                Save
              </a>
            </div>
          )}
          {job.items[0]!.error && (
            <ItemMessage stage={job.items[0]!.stage} error={job.items[0]!.error} />
          )}
        </>
      )}

      {job.error && <p className="mt-2 text-[0.78rem] text-red-400">{job.error}</p>}
    </div>
  )
}
