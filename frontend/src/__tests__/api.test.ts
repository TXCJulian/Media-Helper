import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock fetch globally
const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

// Must import after mocking
const { fetchJson, postForm, postRefresh, fetchPreviewStatus } = await import('@/lib/api')

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  mockFetch.mockReset()
})

describe('fetchJson', () => {
  it('returns parsed JSON on success', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ directories: ['a', 'b'] }))
    const result = await fetchJson<{ directories: string[] }>('/test')
    expect(result.directories).toEqual(['a', 'b'])
  })

  it('appends query params', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({}))
    await fetchJson('/test', { key: 'value' })
    const calledUrl = mockFetch.mock.calls[0]![0] as URL
    expect(calledUrl.toString()).toContain('key=value')
  })

  it('throws on non-ok response with detail', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Invalid path' }), {
        status: 400,
        statusText: 'Bad Request',
      }),
    )
    await expect(fetchJson('/test')).rejects.toThrow('Invalid path')
  })

  it('throws on non-ok response without JSON body', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response('not json', { status: 500, statusText: 'Internal Server Error' }),
    )
    await expect(fetchJson('/test')).rejects.toThrow('HTTP 500')
  })
})

describe('postForm', () => {
  it('sends FormData and returns JSON', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ success: true }))
    const result = await postForm<{ success: boolean }>('/test', { key: 'val', num: 42 })
    expect(result.success).toBe(true)
    const [, options] = mockFetch.mock.calls[0]!
    expect(options.method).toBe('POST')
    expect(options.body).toBeInstanceOf(FormData)
  })
})

describe('postRefresh', () => {
  it('posts to refresh endpoint', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ status: 'ok' }))
    await postRefresh()
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })

  it('throws on error', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response('', { status: 500, statusText: 'Internal Server Error' }),
    )
    await expect(postRefresh()).rejects.toThrow()
  })
})

describe('fetchPreviewStatus', () => {
  it('calls preview-status endpoint and returns payload', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        state: 'running',
        ready: false,
        percent: 12.5,
        eta_seconds: 22,
        elapsed_seconds: 3,
        message: 'Transcoding preview',
      }),
    )

    const result = await fetchPreviewStatus('abc123')
    expect(result.percent).toBe(12.5)

    const calledUrl = mockFetch.mock.calls[0]![0] as URL
    expect(calledUrl.toString()).toContain('/cutter/preview-status/abc123')
  })
})
