import { describe, expect, it } from 'vitest'
import { applyStreamEvent } from '@/hooks/useDownloadStream'
import type { DownloadJob } from '@/types'

function job(id: string, stage: DownloadJob['stage'] = 'queued'): DownloadJob {
  return {
    job_id: id,
    url: `https://example.com/${id}`,
    stage,
    error: null,
    created_at: '2026-08-04 10:00:00',
    updated_at: '2026-08-04 10:00:00',
    items: [],
    has_transcode: false,
  }
}

describe('applyStreamEvent', () => {
  it('replaces all state on a snapshot', () => {
    const next = applyStreamEvent(
      [job('stale')],
      JSON.stringify({
        type: 'snapshot',
        jobs: [job('a'), job('b')],
      }),
    )
    expect(next.map((j) => j.job_id)).toEqual(['a', 'b'])
  })

  it('updates an existing job in place without reordering', () => {
    const next = applyStreamEvent(
      [job('a'), job('b')],
      JSON.stringify({ type: 'job', job: job('a', 'downloading') }),
    )
    expect(next.map((j) => j.job_id)).toEqual(['a', 'b'])
    expect(next[0]!.stage).toBe('downloading')
  })

  it('prepends a job it has not seen before', () => {
    const next = applyStreamEvent([job('a')], JSON.stringify({ type: 'job', job: job('new') }))
    expect(next.map((j) => j.job_id)).toEqual(['new', 'a'])
  })

  it('ignores malformed payloads', () => {
    const before = [job('a')]
    expect(applyStreamEvent(before, 'not json')).toBe(before)
    expect(applyStreamEvent(before, JSON.stringify({ type: 'unknown' }))).toBe(before)
  })

  it('a snapshot after reconnect does not duplicate jobs', () => {
    let state = applyStreamEvent([], JSON.stringify({ type: 'snapshot', jobs: [job('a')] }))
    state = applyStreamEvent(state, JSON.stringify({ type: 'snapshot', jobs: [job('a')] }))
    expect(state).toHaveLength(1)
  })
})
