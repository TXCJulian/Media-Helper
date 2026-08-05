import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseSSEChunk } from '@/lib/sse'
import { openEventStream } from '@/lib/eventStream'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('parseSSEChunk', () => {
  it('extracts complete frames and keeps the remainder', () => {
    const { events, rest } = parseSSEChunk('data: one\n\ndata: tw')
    expect(events).toEqual([{ type: 'message', data: 'one' }])
    expect(rest).toBe('data: tw')
  })

  it('reads a named event type', () => {
    const { events } = parseSSEChunk('event: progress\ndata: {"a":1}\n\n')
    expect(events).toEqual([{ type: 'progress', data: '{"a":1}' }])
  })

  it('ignores comment-only heartbeat frames', () => {
    const { events } = parseSSEChunk(': heartbeat\n\n')
    expect(events).toEqual([])
  })

  it('joins multi-line data payloads', () => {
    const { events } = parseSSEChunk('data: a\ndata: b\n\n')
    expect(events).toEqual([{ type: 'message', data: 'a\nb' }])
  })
})

function streamOf(chunks: string[]): Response {
  const encoder = new TextEncoder()
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]!) }
            : { done: true, value: undefined },
        cancel: async () => {},
      }),
    },
  } as unknown as Response
}

describe('openEventStream', () => {
  it('delivers each event payload to the callback', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamOf(['data: one\n\ndata: two\n\n'])))
    const seen: string[] = []
    const close = openEventStream('/download/events', (d) => seen.push(d), { retryMs: 10_000 })

    await vi.waitFor(() => expect(seen).toEqual(['one', 'two']))
    close()
  })

  it('reports connection state transitions', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamOf(['data: x\n\n'])))
    const states: boolean[] = []
    const close = openEventStream('/download/events', () => {}, {
      onStateChange: (c) => states.push(c),
      retryMs: 10_000,
    })

    await vi.waitFor(() => expect(states[0]).toBe(true))
    close()
  })

  it('reconnects after the stream ends', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(streamOf(['data: first\n\n']))
      .mockResolvedValue(streamOf(['data: second\n\n']))
    vi.stubGlobal('fetch', fetchMock)

    const seen: string[] = []
    const close = openEventStream('/download/events', (d) => seen.push(d), { retryMs: 1 })

    await vi.waitFor(() => expect(seen).toContain('second'), { timeout: 2000 })
    close()
  })

  it('stops reconnecting once closed', async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamOf(['data: x\n\n']))
    vi.stubGlobal('fetch', fetchMock)

    const close = openEventStream('/download/events', () => {}, { retryMs: 1 })
    close()
    const callsAfterClose = fetchMock.mock.calls.length
    await new Promise((r) => setTimeout(r, 50))
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(callsAfterClose + 1)
  })
})
