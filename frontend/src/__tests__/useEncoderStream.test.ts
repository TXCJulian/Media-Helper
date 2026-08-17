import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { applyEncoderStreamEvent, useEncoderStream } from '@/hooks/useEncoderStream'
import { openEncoderStream } from '@/lib/api'
import type { EncoderJob } from '@/types'

const stream = vi.hoisted(() => ({
  onEvent: undefined as undefined | ((data: string) => void),
  onStateChange: undefined as undefined | ((connected: boolean) => void),
  close: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
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
})

describe('useEncoderStream', () => {
  it('connects once and exposes streamed jobs and connection status', () => {
    const { result, unmount } = renderHook(() => useEncoderStream())

    expect(openEncoderStream).toHaveBeenCalledTimes(1)
    expect(result.current).toEqual({ jobs: [], connected: false })

    act(() => {
      stream.onStateChange?.(true)
      stream.onEvent?.(JSON.stringify(job()))
    })

    expect(result.current.connected).toBe(true)
    expect(result.current.jobs).toEqual([job()])

    unmount()
    expect(stream.close).toHaveBeenCalledTimes(1)
  })
})
