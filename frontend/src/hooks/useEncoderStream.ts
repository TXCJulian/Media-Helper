import { useEffect, useRef, useState } from 'react'
import { fetchEncoderReprocessStatus, openEncoderStream } from '@/lib/api'
import type { EncoderJob, EncoderReprocessEvent } from '@/types'

const encoderJobStages = new Set<EncoderJob['stage']>([
  'settling',
  'pending',
  'queued',
  'encoding',
  'swapping',
  'done',
  'failed',
  'blocked',
  'cancelled',
  'skipped',
])

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || typeof value === 'number'
}

function isEncoderJob(value: unknown): value is EncoderJob {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false

  const job = value as Record<string, unknown>
  return (
    typeof job.job_id === 'string' &&
    typeof job.source_path === 'string' &&
    typeof job.stage === 'string' &&
    encoderJobStages.has(job.stage as EncoderJob['stage']) &&
    typeof job.progress === 'number' &&
    isNullableString(job.preset_name) &&
    isNullableString(job.rule_id) &&
    isNullableString(job.error) &&
    isNullableString(job.error_code) &&
    isNullableString(job.output_path) &&
    typeof job.facts === 'object' &&
    job.facts !== null &&
    !Array.isArray(job.facts) &&
    isNullableNumber(job.original_size) &&
    isNullableNumber(job.encoded_size) &&
    isNullableNumber(job.saved_bytes) &&
    typeof job.created_at === 'string' &&
    typeof job.updated_at === 'string'
  )
}

function isSnapshot(value: unknown): value is { type: 'snapshot'; jobs: EncoderJob[] } {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const object = value as Record<string, unknown>
  return object.type === 'snapshot' && Array.isArray(object.jobs) && object.jobs.every(isEncoderJob)
}

const encoderReprocessStatuses = new Set<EncoderReprocessEvent['status']>([
  'started',
  'running',
  'completed',
  'failed',
])

function isEncoderReprocessEvent(value: unknown): value is EncoderReprocessEvent {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const event = value as Record<string, unknown>
  return (
    event.type === 'reprocess' &&
    typeof event.run_id === 'string' &&
    event.run_id.length > 0 &&
    typeof event.status === 'string' &&
    encoderReprocessStatuses.has(event.status as EncoderReprocessEvent['status']) &&
    ['scanned', 'created', 'skipped', 'failed'].every(
      (key) => typeof event[key] === 'number' && Number.isInteger(event[key]) && event[key] >= 0,
    ) &&
    isNullableString(event.path) &&
    isNullableString(event.error)
  )
}

/** Fold a server job event into the local stream state. */
export function applyEncoderStreamEvent(jobs: EncoderJob[], raw: string): EncoderJob[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return jobs
  }

  if (isSnapshot(parsed)) return parsed.jobs
  if (
    typeof parsed === 'object' &&
    parsed !== null &&
    !Array.isArray(parsed) &&
    (parsed as Record<string, unknown>).type === 'deleted' &&
    typeof (parsed as Record<string, unknown>).job_id === 'string'
  ) {
    const deletedId = (parsed as Record<string, string>).job_id
    return jobs.filter((job) => job.job_id !== deletedId)
  }

  if (!isEncoderJob(parsed)) return jobs

  const incoming = parsed
  const index = jobs.findIndex((job) => job.job_id === incoming.job_id)
  return index < 0 ? [incoming, ...jobs] : jobs.map((job, i) => (i === index ? incoming : job))
}

export function useEncoderStream(): {
  jobs: EncoderJob[]
  connected: boolean
  snapshotReceived: boolean
  latestReprocessEvent: EncoderReprocessEvent | null
  reprocessActive: boolean | null
} {
  const [jobs, setJobs] = useState<EncoderJob[]>([])
  const [connected, setConnected] = useState(false)
  const [snapshotReceived, setSnapshotReceived] = useState(false)
  const [latestReprocessEvent, setLatestReprocessEvent] = useState<EncoderReprocessEvent | null>(
    null,
  )
  const [reprocessActive, setReprocessActive] = useState<boolean | null>(null)
  const latestReprocessEventRef = useRef<EncoderReprocessEvent | null>(null)

  useEffect(() => {
    return openEncoderStream((data) => {
      try {
        const parsed: unknown = JSON.parse(data)
        if (isSnapshot(parsed)) setSnapshotReceived(true)
        if (isEncoderReprocessEvent(parsed)) {
          latestReprocessEventRef.current = parsed
          setLatestReprocessEvent(parsed)
          setReprocessActive(parsed.status === 'started' || parsed.status === 'running')
        }
      } catch {
        // applyEncoderStreamEvent owns malformed-frame handling.
      }
      setJobs((previous) => applyEncoderStreamEvent(previous, data))
    }, setConnected)
  }, [])

  useEffect(() => {
    if (!connected) return
    let current = true
    void fetchEncoderReprocessStatus()
      .then((status) => {
        if (!current) return
        const latest = latestReprocessEventRef.current
        const recovered = status.event
        if (
          latest &&
          recovered &&
          latest.run_id === recovered.run_id &&
          (latest.status === 'completed' || latest.status === 'failed') &&
          (recovered.status === 'started' || recovered.status === 'running')
        ) {
          return
        }
        latestReprocessEventRef.current = recovered
        setReprocessActive(status.active)
        setLatestReprocessEvent(recovered)
      })
      .catch(() => {
        // SSE remains authoritative while the lightweight recovery request is unavailable.
      })
    return () => {
      current = false
    }
  }, [connected])

  return { jobs, connected, snapshotReceived, latestReprocessEvent, reprocessActive }
}
