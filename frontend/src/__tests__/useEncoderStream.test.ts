import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { applyEncoderStreamEvent, useEncoderStream } from '@/hooks/useEncoderStream'
import { fetchEncoderReprocessStatus, openEncoderStream } from '@/lib/api'
import type { EncoderJob, EncoderReprocessEvent } from '@/types'

const stream = vi.hoisted(() => ({
  onEvent: undefined as undefined | ((data: string) => void),
  onStateChange: undefined as undefined | ((connected: boolean) => void),
  close: vi.fn(),
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

vi.mock('@/lib/api', () => ({
  fetchEncoderReprocessStatus: vi.fn().mockResolvedValue({ active: false, event: null }),
  openEncoderStream: vi.fn(
    (onEvent: (data: string) => void, onStateChange?: (connected: boolean) => void) => {
      stream.onEvent = onEvent
      stream.onStateChange = onStateChange
      return stream.close
    },
  ),
}))

function job(overrides: Partial<EncoderJob> = {}): EncoderJob {
  return {
    job_id: 'job-1',
    source_path: '/media/movie.mkv',
    stage: 'encoding',
    progress: 10,
    preset_name: 'NVENC',
    rule_id: 'rule-1',
    error: null,
    error_code: null,
    output_path: null,
    facts: {},
    original_size: null,
    encoded_size: null,
    saved_bytes: null,
    created_at: '2026-08-17T10:00:00Z',
    updated_at: '2026-08-17T10:00:00Z',
    ...overrides,
  }
}

afterEach(() => {
  vi.clearAllMocks()
  stream.onEvent = undefined
  stream.onStateChange = undefined
})

describe('applyEncoderStreamEvent', () => {
  it('replaces a job from an SSE event', () => {
    const next = applyEncoderStreamEvent(
      [job({ progress: 10 })],
      JSON.stringify(job({ progress: 45 })),
    )

    expect(next[0]!.progress).toBe(45)
  })

  it('prepends a newly observed job', () => {
    const next = applyEncoderStreamEvent([job()], JSON.stringify(job({ job_id: 'new' })))

    expect(next.map((item) => item.job_id)).toEqual(['new', 'job-1'])
  })

  it('ignores malformed JSON and heartbeat data without replacing state', () => {
    const before = [job()]

    expect(applyEncoderStreamEvent(before, 'not json')).toBe(before)
    expect(applyEncoderStreamEvent(before, JSON.stringify({ type: 'heartbeat' }))).toBe(before)
  })

  it('ignores an incomplete job payload without replacing state', () => {
    const before = [job()]

    expect(applyEncoderStreamEvent(before, JSON.stringify({ job_id: 'incomplete' }))).toBe(before)
  })

  it('replaces stale state with the authoritative snapshot', () => {
    const next = applyEncoderStreamEvent(
      [job({ job_id: 'stale' })],
      JSON.stringify({ type: 'snapshot', jobs: [job({ job_id: 'current' })] }),
    )

    expect(next.map((item) => item.job_id)).toEqual(['current'])
  })

  it('removes a job from a deletion delta', () => {
    const next = applyEncoderStreamEvent(
      [job(), job({ job_id: 'keep' })],
      JSON.stringify({ type: 'deleted', job_id: 'job-1' }),
    )

    expect(next.map((item) => item.job_id)).toEqual(['keep'])
  })
})

describe('useEncoderStream', () => {
  it('connects once and exposes streamed jobs and connection status', () => {
    const { result, unmount } = renderHook(() => useEncoderStream())

    expect(openEncoderStream).toHaveBeenCalledTimes(1)
    expect(result.current).toEqual({
      jobs: [],
      connected: false,
      snapshotReceived: false,
      latestReprocessEvent: null,
      reprocessActive: null,
    })

    act(() => {
      stream.onStateChange?.(true)
      stream.onEvent?.(JSON.stringify(job()))
    })

    expect(result.current.connected).toBe(true)
    expect(result.current.jobs).toEqual([job()])
    expect(result.current.snapshotReceived).toBe(false)

    unmount()
    expect(stream.close).toHaveBeenCalledTimes(1)
  })

  it('recovers bulk status whenever the event stream reconnects', async () => {
    const terminal: EncoderReprocessEvent = {
      type: 'reprocess',
      run_id: 'bulk-1',
      status: 'completed',
      scanned: 2,
      created: 1,
      skipped: 1,
      failed: 0,
      path: null,
      error: null,
    }
    vi.mocked(fetchEncoderReprocessStatus).mockResolvedValue({ active: false, event: terminal })
    const { result } = renderHook(() => useEncoderStream())

    await act(async () => stream.onStateChange?.(true))

    expect(fetchEncoderReprocessStatus).toHaveBeenCalledTimes(1)
    expect(result.current.latestReprocessEvent).toEqual(terminal)
    expect(result.current.reprocessActive).toBe(false)
  })

  it('does not replace a newer SSE run with an older status response', async () => {
    const status = deferred<{
      active: boolean
      event: EncoderReprocessEvent
    }>()
    vi.mocked(fetchEncoderReprocessStatus).mockReturnValue(status.promise)
    const { result } = renderHook(() => useEncoderStream())
    const newer: EncoderReprocessEvent = {
      type: 'reprocess',
      run_id: 'bulk-2',
      status: 'running',
      scanned: 2,
      created: 1,
      skipped: 1,
      failed: 0,
      path: null,
      error: null,
    }
    const stale: EncoderReprocessEvent = {
      ...newer,
      run_id: 'bulk-1',
      status: 'completed',
    }

    act(() => stream.onStateChange?.(true))
    act(() => stream.onEvent?.(JSON.stringify(newer)))
    await act(async () => status.resolve({ active: false, event: stale }))

    expect(result.current.latestReprocessEvent).toEqual(newer)
    expect(result.current.reprocessActive).toBe(true)
  })

  it('marks the stream authoritative after receiving a snapshot envelope', () => {
    const { result } = renderHook(() => useEncoderStream())
    act(() => {
      stream.onEvent?.(JSON.stringify({ type: 'snapshot', jobs: [job()] }))
    })
    expect(result.current.snapshotReceived).toBe(true)
    expect(result.current.jobs).toEqual([job()])
  })

  it('exposes a validated reprocess event without folding it into jobs', () => {
    const { result } = renderHook(() => useEncoderStream())
    const event: EncoderReprocessEvent = {
      type: 'reprocess',
      run_id: 'bulk-1',
      status: 'running',
      scanned: 12,
      created: 4,
      skipped: 7,
      failed: 1,
      path: '/media/Movies/Demo.mkv',
      error: null,
    }

    act(() => {
      stream.onEvent?.(JSON.stringify(event))
    })

    expect(result.current.latestReprocessEvent).toEqual(event)
    expect(result.current.jobs).toEqual([])
  })
})
