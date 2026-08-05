import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  cancelDownloadJob,
  createDownloads,
  getDownloadItemFileUrl,
  startDownloadJob,
} from '@/lib/api'

afterEach(() => {
  vi.restoreAllMocks()
})

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

describe('createDownloads', () => {
  it('sends every url in a single JSON request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ job_ids: ['a', 'b'] }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await createDownloads(['https://x/1', 'https://x/2'], { type: 'audio' })

    expect(result.job_ids).toEqual(['a', 'b'])
    expect(fetchMock).toHaveBeenCalledTimes(1)

    const [, init] = fetchMock.mock.calls[0]!
    expect(init.method).toBe('POST')
    const body = JSON.parse(init.body as string)
    expect(body.urls).toEqual(['https://x/1', 'https://x/2'])
    expect(body.options.type).toBe('audio')
  })
})

describe('cancelDownloadJob', () => {
  it('posts to the cancel endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'cancelled' }))
    vi.stubGlobal('fetch', fetchMock)

    await cancelDownloadJob('job-1')

    expect(fetchMock.mock.calls[0]![0]).toContain('/download/jobs/job-1/cancel')
  })
})

describe('startDownloadJob', () => {
  it('posts to the start endpoint and returns a promise', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'started' }))
    vi.stubGlobal('fetch', fetchMock)

    await startDownloadJob('job-1')

    expect(fetchMock.mock.calls[0]![0]).toContain('/download/jobs/job-1/start')
  })
})

describe('getDownloadItemFileUrl', () => {
  it('encodes the job id and includes the item index', () => {
    expect(getDownloadItemFileUrl('a b', 2)).toBe('/download/jobs/a%20b/items/2/file')
  })
})
