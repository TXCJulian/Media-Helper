import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import EncoderPanel from '@/components/EncoderPanel'
import Landing from '@/components/Landing'
import type { EncoderJob } from '@/types'

const stream = vi.hoisted(() => ({ jobs: [] as unknown[], connected: true }))

vi.mock('@/hooks/useEncoderStream', () => ({
  useEncoderStream: () => ({ jobs: stream.jobs, connected: stream.connected }),
}))

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function noContentResponse(): Response {
  return {
    ok: true,
    status: 204,
    statusText: 'No Content',
    headers: new Headers(),
    text: async () => '',
  } as unknown as Response
}

function job(overrides: Partial<EncoderJob> = {}): EncoderJob {
  return {
    job_id: 'job-1',
    source_path: '/media/Movies/Demo.mkv',
    stage: 'encoding',
    progress: 42,
    preset_name: 'QSV',
    rule_id: 'uhd',
    error: null,
    error_code: null,
    output_path: null,
    facts: {},
    original_size: null,
    encoded_size: null,
    saved_bytes: null,
    created_at: '2026-08-17T08:00:00Z',
    updated_at: '2026-08-17T08:01:00Z',
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function responseFor(url: string, jobs: EncoderJob[] = []): Response {
  if (url.includes('/encoder/config')) {
    return jsonResponse({
      watch_paths: ['/media/Movies'],
      mode: 'review',
      settle_seconds: 30,
      original_ttl: 604800,
      job_ttl: 604800,
    })
  }
  if (/\/encoder\/presets\/[^/?]+/.test(url)) {
    return jsonResponse({
      body: {
        PresetName: 'QSV',
        VideoEncoder: 'qsv_h265',
        VideoPreset: 'balanced',
        FileFormat: 'av_mkv',
      },
    })
  }
  if (url.includes('/encoder/presets')) {
    return jsonResponse([
      { name: 'QSV', encoder: 'qsv_h265', video_preset: 'balanced', file_format: 'av_mkv' },
    ])
  }
  if (url.includes('/encoder/rules')) {
    return jsonResponse({ rules: [], fallback: 'skip' })
  }
  if (url.includes('/encoder/jobs')) return jsonResponse(jobs)
  throw new Error(`Unexpected request: ${url}`)
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  stream.jobs = []
  stream.connected = true
})

describe('encoder navigation', () => {
  it('shows Auto Encoder only when enabled and navigates to it', () => {
    const onNavigate = vi.fn()
    const rendered = render(
      <Landing enabledFeatures={['encoder']} backendStatus="connected" onNavigate={onNavigate} />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Auto Encoder/i }))
    expect(onNavigate).toHaveBeenCalledWith('encoder')

    rendered.rerender(
      <Landing enabledFeatures={[]} backendStatus="connected" onNavigate={onNavigate} />,
    )
    expect(screen.queryByRole('button', { name: /Auto Encoder/i })).toBeNull()
  })
})

describe('EncoderPanel', () => {
  it('retries only an offline health pill', async () => {
    let healthRequests = 0
    const fetchMock = vi.fn(async (request: string | URL | Request) => {
      const url = String(request)
      if (url.includes('/encoder/health')) {
        healthRequests += 1
        if (healthRequests === 1) throw new Error('offline')
        return jsonResponse({ status: 'ok', vendor: 'QSV', encoders: ['qsv_h265'] })
      }
      return responseFor(url)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<EncoderPanel onBack={vi.fn()} />)

    const offline = await screen.findByRole('button', { name: /Offline/i })
    await screen.findByRole('region', { name: 'Encoder settings' })
    fireEvent.click(screen.getByRole('button', { name: 'Presets' }))
    expect(screen.getByRole('button', { name: 'Edit QSV' }).hasAttribute('disabled')).toBe(false)
    const requestsBeforeRetry = fetchMock.mock.calls.map(([request]) => String(request))
    fireEvent.click(offline)
    expect(await screen.findByRole('button', { name: /^QSV$/ })).toBeTruthy()
    expect(healthRequests).toBe(2)
    for (const endpoint of [
      '/encoder/config',
      '/encoder/presets',
      '/encoder/rules',
      '/encoder/jobs',
    ]) {
      expect(
        fetchMock.mock.calls.filter(([request]) => String(request).includes(endpoint)),
      ).toHaveLength(requestsBeforeRetry.filter((request) => request.includes(endpoint)).length)
    }
  })

  it('shows active jobs before collapsed history', async () => {
    const jobs = [
      job({ job_id: 'active', source_path: '/media/Movies/Active.mkv' }),
      job({
        job_id: 'finished',
        source_path: '/media/Movies/Finished.mkv',
        stage: 'done',
        progress: 100,
      }),
    ]
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: string | URL | Request) => {
        const url = String(request)
        if (url.includes('/encoder/health')) {
          return jsonResponse({ status: 'ok', vendor: 'QSV', encoders: ['qsv_h265'] })
        }
        return responseFor(url, jobs)
      }),
    )

    render(<EncoderPanel onBack={vi.fn()} />)

    expect(await screen.findByText('Active.mkv')).toBeTruthy()
    expect(screen.queryByText('Finished.mkv')).toBeNull()
    const history = screen.getByRole('button', { name: /History \(1\)/i })
    expect(
      screen.getByText('Active.mkv').compareDocumentPosition(history) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()

    fireEvent.click(history)
    expect(await screen.findByText('Finished.mkv')).toBeTruthy()
    await waitFor(() => expect(screen.getByText(/Live updates/i)).toBeTruthy())
  })

  it('keeps the freshest job when an older stream snapshot arrives', async () => {
    stream.jobs = [job({ progress: 10, updated_at: '2026-08-17T08:01:00Z' })]
    const latest = job({ progress: 80, updated_at: '2026-08-17T08:02:00Z' })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: string | URL | Request) => {
        const url = String(request)
        if (url.includes('/encoder/health')) {
          return jsonResponse({ status: 'ok', vendor: 'QSV', encoders: ['qsv_h265'] })
        }
        return responseFor(url, [latest])
      }),
    )

    render(<EncoderPanel onBack={vi.fn()} />)

    expect((await screen.findByRole('progressbar')).getAttribute('aria-valuenow')).toBe('80')
  })

  it('applies one acknowledged approval while live updates are disconnected', async () => {
    stream.connected = false
    const pending = job({ stage: 'pending', progress: 0 })
    const approval = deferred<Response>()
    const fetchMock = vi.fn(async (request: string | URL | Request) => {
      const url = String(request)
      if (url.includes('/approve')) return approval.promise
      if (url.includes('/encoder/health')) {
        return jsonResponse({ status: 'ok', vendor: 'QSV', encoders: ['qsv_h265'] })
      }
      return responseFor(url, [pending])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<EncoderPanel onBack={vi.fn()} />)

    const approve = await screen.findByRole('button', { name: 'Approve encoding' })
    fireEvent.click(approve)
    fireEvent.click(approve)
    expect(
      fetchMock.mock.calls.filter(([request]) => String(request).includes('/approve')),
    ).toHaveLength(1)

    await act(async () => approval.resolve(jsonResponse({ stage: 'queued' })))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Approve encoding' })).toBeNull(),
    )
    expect(screen.getByText('Reconnecting…')).toBeTruthy()
  })

  it('moves a recovered blocked job into history without waiting for SSE', async () => {
    stream.connected = false
    const blocked = job({ stage: 'blocked', progress: 0, error_code: 'offline' })
    const fetchMock = vi.fn(async (request: string | URL | Request, init?: RequestInit) => {
      const url = String(request)
      if (url.includes('/encoder/health')) {
        return jsonResponse({ status: 'ok', vendor: 'QSV', encoders: ['qsv_h265'] })
      }
      if (url.includes('/encoder/jobs/job-1/reprocess') && init?.method === 'POST') {
        return jsonResponse({
          job_id: 'replacement',
          path: blocked.source_path,
          stage: 'pending',
          created: true,
        })
      }
      return responseFor(url, [blocked])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<EncoderPanel onBack={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Re-evaluate file' }))

    await waitFor(() => expect(screen.queryByText('Demo.mkv')).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: /History \(1\)/i }))
    expect(await screen.findByText('Demo.mkv')).toBeTruthy()
    expect(screen.getByText('Cancelled')).toBeTruthy()
  })

  it('keeps a job-action failure when a settings refresh completes later', async () => {
    const pending = job({ stage: 'pending', progress: 0 })
    const refreshedConfig = deferred<Response>()
    let configRequests = 0
    const fetchMock = vi.fn(async (request: string | URL | Request, init?: RequestInit) => {
      const url = String(request)
      if (url.includes('/encoder/health')) {
        return jsonResponse({ status: 'ok', vendor: 'QSV', encoders: ['qsv_h265'] })
      }
      if (url.includes('/encoder/jobs/') && url.includes('/approve')) {
        return jsonResponse({ reason: 'approval failed' }, 409)
      }
      if (url.includes('/encoder/presets/QSV') && init?.method === 'DELETE') {
        return noContentResponse()
      }
      if (url.includes('/encoder/config')) {
        configRequests += 1
        if (configRequests === 2) return refreshedConfig.promise
      }
      return responseFor(url, [pending])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<EncoderPanel onBack={vi.fn()} />)

    await screen.findByRole('button', { name: 'Approve encoding' })
    fireEvent.click(screen.getByRole('button', { name: 'Presets' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete QSV' }))
    await waitFor(() => expect(configRequests).toBe(2))

    fireEvent.click(screen.getByRole('button', { name: 'Approve encoding' }))
    expect((await screen.findByRole('alert')).textContent).toContain('approval failed')

    await act(async () =>
      refreshedConfig.resolve(
        jsonResponse({
          watch_paths: ['/media/Updated'],
          mode: 'review',
          settle_seconds: 30,
          original_ttl: 604800,
          job_ttl: 604800,
        }),
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Watch Folders' }))
    expect((await screen.findByLabelText('Watch folder 1')).getAttribute('value')).toBe(
      '/media/Updated',
    )
    expect(screen.getByRole('alert').textContent).toContain('approval failed')
  })

  it('keeps a settings-action failure when a different settings action refreshes', async () => {
    const refreshedConfig = deferred<Response>()
    let configRequests = 0
    const fetchMock = vi.fn(async (request: string | URL | Request, init?: RequestInit) => {
      const url = String(request)
      if (url.includes('/encoder/health')) {
        return jsonResponse({ status: 'ok', vendor: 'QSV', encoders: ['qsv_h265'] })
      }
      if (url.includes('/encoder/rules') && init?.method === 'PUT') {
        return jsonResponse({ reason: 'rules save failed' }, 400)
      }
      if (url.includes('/encoder/presets/QSV') && init?.method === 'DELETE') {
        return noContentResponse()
      }
      if (url.includes('/encoder/config')) {
        configRequests += 1
        if (configRequests === 2) return refreshedConfig.promise
      }
      return responseFor(url)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<EncoderPanel onBack={vi.fn()} />)

    await screen.findByRole('region', { name: 'Encoder settings' })
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save rules' }))
    expect((await screen.findByRole('alert')).textContent).toContain('rules save failed')

    fireEvent.click(screen.getByRole('button', { name: 'Presets' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete QSV' }))
    await waitFor(() => expect(configRequests).toBe(2))
    await act(async () =>
      refreshedConfig.resolve(
        jsonResponse({
          watch_paths: ['/media/Settings-refreshed'],
          mode: 'review',
          settle_seconds: 30,
          original_ttl: 604800,
          job_ttl: 604800,
        }),
      ),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Watch Folders' }))
    expect((await screen.findByLabelText('Watch folder 1')).getAttribute('value')).toBe(
      '/media/Settings-refreshed',
    )
    expect(screen.getByRole('alert').textContent).toContain('rules save failed')
  })

  it('keeps a delete-action failure when a settings refresh completes later', async () => {
    const refreshedConfig = deferred<Response>()
    let configRequests = 0
    const fetchMock = vi.fn(async (request: string | URL | Request, init?: RequestInit) => {
      const url = String(request)
      if (url.includes('/encoder/health')) {
        return jsonResponse({ status: 'ok', vendor: 'QSV', encoders: ['qsv_h265'] })
      }
      if (url.includes('/encoder/jobs/job-1') && init?.method === 'DELETE') {
        return jsonResponse({ reason: 'delete failed' }, 409)
      }
      if (url.includes('/encoder/presets/QSV') && init?.method === 'DELETE') {
        return noContentResponse()
      }
      if (url.includes('/encoder/config')) {
        configRequests += 1
        if (configRequests === 2) return refreshedConfig.promise
      }
      return responseFor(url, [job()])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<EncoderPanel onBack={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Delete job' }))
    expect((await screen.findByRole('alert')).textContent).toContain('delete failed')

    fireEvent.click(screen.getByRole('button', { name: 'Presets' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete QSV' }))
    await waitFor(() => expect(configRequests).toBe(2))
    await act(async () =>
      refreshedConfig.resolve(
        jsonResponse({
          watch_paths: ['/media/Delete-refreshed'],
          mode: 'review',
          settle_seconds: 30,
          original_ttl: 604800,
          job_ttl: 604800,
        }),
      ),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Watch Folders' }))
    expect((await screen.findByLabelText('Watch folder 1')).getAttribute('value')).toBe(
      '/media/Delete-refreshed',
    )
    expect(screen.getByRole('alert').textContent).toContain('delete failed')
  })
})
