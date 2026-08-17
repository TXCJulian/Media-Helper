import { useEffect, useState } from 'react'
import { openEncoderStream } from '@/lib/api'
import type { EncoderJob } from '@/types'

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
} {
  const [jobs, setJobs] = useState<EncoderJob[]>([])
  const [connected, setConnected] = useState(false)
  const [snapshotReceived, setSnapshotReceived] = useState(false)

  useEffect(() => {
    return openEncoderStream((data) => {
      try {
        const parsed: unknown = JSON.parse(data)
        if (isSnapshot(parsed)) setSnapshotReceived(true)
      } catch {
        // applyEncoderStreamEvent owns malformed-frame handling.
      }
      setJobs((previous) => applyEncoderStreamEvent(previous, data))
    }, setConnected)
  }, [])

  return { jobs, connected, snapshotReceived }
}
