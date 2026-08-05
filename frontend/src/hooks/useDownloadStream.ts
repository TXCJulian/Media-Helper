import { useEffect, useState } from 'react'
import { openDownloadStream } from '@/lib/api'
import type { DownloadJob } from '@/types'

interface SnapshotEvent {
  type: 'snapshot'
  jobs: DownloadJob[]
}

interface JobEvent {
  type: 'job'
  job: DownloadJob
}

/**
 * A job that no longer exists server-side — deleted by the user, or swept by
 * the TTL purge. It carries only an id because there is no job left to send.
 */
interface JobDeletedEvent {
  type: 'job_deleted'
  job_id: string
}

/**
 * Fold one server event into job state.
 *
 * The server is the only writer: a snapshot replaces everything, a job event
 * replaces exactly one entry, and a deletion drops one. Returning the
 * previous array unchanged for unrecognised payloads — and for a deletion of
 * something we do not hold — keeps React from re-rendering on noise.
 */
export function applyStreamEvent(jobs: DownloadJob[], raw: string): DownloadJob[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return jobs
  }

  if (typeof parsed !== 'object' || parsed === null || !('type' in parsed)) {
    return jobs
  }

  const event = parsed as { type: unknown }

  if (event.type === 'snapshot') {
    const snapshot = parsed as Partial<SnapshotEvent>
    if (Array.isArray(snapshot.jobs)) {
      return snapshot.jobs
    }
    return jobs
  }

  if (event.type === 'job') {
    const jobEvent = parsed as Partial<JobEvent>
    const incoming = jobEvent.job
    if (!incoming || typeof incoming.job_id !== 'string') {
      return jobs
    }
    const index = jobs.findIndex((j) => j.job_id === incoming.job_id)
    if (index === -1) return [incoming, ...jobs]
    const next = jobs.slice()
    next[index] = incoming
    return next
  }

  if (event.type === 'job_deleted') {
    const deleted = parsed as Partial<JobDeletedEvent>
    if (typeof deleted.job_id !== 'string') {
      return jobs
    }
    const remaining = jobs.filter((j) => j.job_id !== deleted.job_id)
    return remaining.length === jobs.length ? jobs : remaining
  }

  return jobs
}

export function useDownloadStream(): { jobs: DownloadJob[]; connected: boolean } {
  const [jobs, setJobs] = useState<DownloadJob[]>([])
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    return openDownloadStream(
      (data) => setJobs((prev) => applyStreamEvent(prev, data)),
      setConnected,
    )
  }, [])

  return { jobs, connected }
}
