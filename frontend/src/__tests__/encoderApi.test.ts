import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  approveEncoderJob,
  deleteEncoderJob,
  deleteEncoderPreset,
  fetchEncoderConfig,
  fetchEncoderHealth,
  fetchEncoderJobs,
  fetchEncoderPreset,
  fetchEncoderPresets,
  fetchEncoderRules,
  importEncoderPresets,
  previewEncoderPresets,
  reprocessEncoderFile,
  reprocessEncoderJob,
  saveEncoderConfig,
  saveEncoderPreset,
  saveEncoderRules,
  testEncoderFile,
} from '@/lib/api'

afterEach(() => {
  vi.restoreAllMocks()
})

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function noContentResponse() {
  return {
    ok: true,
    status: 204,
    statusText: 'No Content',
    headers: new Headers(),
    json: vi.fn(),
    text: async () => '',
  } as unknown as Response
}

function bodyOf(fetchMock: ReturnType<typeof vi.fn>, call = 0): unknown {
  const [, init] = fetchMock.mock.calls[call]!
  return JSON.parse(init.body as string)
}

describe('encoder configuration transport', () => {
  it('saves an empty watch list explicitly', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ watch_paths: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await saveEncoderConfig([])

    const [, init] = fetchMock.mock.calls[0]!
    expect(init.method).toBe('PUT')
    expect(bodyOf(fetchMock)).toEqual({ watch_paths: [] })
  })

  it('loads encoder health and configuration from their endpoints', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'ok', vendor: 'QSV', encoders: ['qsv_h265'] }))
      .mockResolvedValueOnce(
        jsonResponse({
          watch_paths: [],
          mode: 'auto',
          settle_seconds: 30,
          original_ttl: 1,
          job_ttl: 2,
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchEncoderHealth()).resolves.toMatchObject({ vendor: 'QSV' })
    await expect(fetchEncoderConfig()).resolves.toMatchObject({ watch_paths: [] })

    expect(new URL(String(fetchMock.mock.calls[0]![0])).pathname).toBe('/api/encoder/health')
    expect(new URL(String(fetchMock.mock.calls[1]![0])).pathname).toBe('/api/encoder/config')
  })

  it('preserves an encoder error reason for the caller', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ code: 'invalid_watch_path', reason: 'Path is outside BASE_PATHS' }, 400),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(saveEncoderConfig(['/outside'])).rejects.toThrow('Path is outside BASE_PATHS')
  })
})

describe('encoder preset transport', () => {
  it('uses JSON for preset save, preview, and selected document import', async () => {
    const leaf = { PresetName: 'NVENC', VideoEncoder: 'nvenc_h265' }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ body: leaf }))
      .mockResolvedValueOnce(jsonResponse({ presets: [] }))
      .mockResolvedValueOnce(jsonResponse({ imported: ['NVENC'], skipped: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await saveEncoderPreset('NVENC HD', leaf)
    expect(new URL(String(fetchMock.mock.calls[0]![0])).pathname).toBe(
      '/api/encoder/presets/NVENC%20HD',
    )
    expect(bodyOf(fetchMock)).toEqual({ body: leaf })

    await previewEncoderPresets({ PresetList: [leaf] })
    const [, init] = fetchMock.mock.calls[1]!
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ document: { PresetList: [leaf] } })

    await importEncoderPresets({ PresetList: [leaf] }, ['NVENC'])
    expect(JSON.parse(fetchMock.mock.calls[2]![1].body as string)).toEqual({
      document: { PresetList: [leaf] },
      include_names: ['NVENC'],
    })
  })

  it('loads preset summaries and a complete leaf', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([{ name: 'P', encoder: 'x264', video_preset: 'fast', file_format: 'av_mkv' }]),
      )
      .mockResolvedValueOnce(
        jsonResponse({ body: { PresetName: 'P', AudioCopyMask: ['copy:aac'] } }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchEncoderPresets()).resolves.toHaveLength(1)
    await expect(fetchEncoderPreset('P')).resolves.toMatchObject({
      body: { AudioCopyMask: ['copy:aac'] },
    })
  })

  it('deletes a preset through its no-content endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(noContentResponse())
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteEncoderPreset('P')).resolves.toBeUndefined()

    const [, init] = fetchMock.mock.calls[0]!
    expect(init.method).toBe('DELETE')
  })
})

describe('encoder rule and job transport', () => {
  it('uses JSON for rules, file actions, and approval', async () => {
    const rules = { rules: [{ id: 'hd', conditions: [], target: 'NVENC' }], fallback: 'skip' }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ saved: 1 }))
      .mockResolvedValueOnce(
        jsonResponse({
          facts: {},
          matched_rule: null,
          target: 'skip',
          evaluated: [],
          not_evaluated: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ job_id: 'new', path: '/media/a.mkv', stage: 'pending', created: true }),
      )
      .mockResolvedValueOnce(jsonResponse({ stage: 'queued' }))
    vi.stubGlobal('fetch', fetchMock)

    await saveEncoderRules(rules)
    expect(bodyOf(fetchMock)).toEqual(rules)

    await testEncoderFile('/media/a.mkv')
    expect(bodyOf(fetchMock, 1)).toEqual({ path: '/media/a.mkv' })

    await reprocessEncoderFile('/media/a.mkv')
    expect(bodyOf(fetchMock, 2)).toEqual({ path: '/media/a.mkv' })

    await approveEncoderJob('job id')
    expect(new URL(String(fetchMock.mock.calls[3]![0])).pathname).toBe(
      '/api/encoder/jobs/job%20id/approve',
    )
    expect(bodyOf(fetchMock, 3)).toEqual({})
  })

  it('reprocesses a failed job through its encoded route', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ job_id: 'replacement', path: '/media/a.mkv', stage: 'pending', created: true }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(reprocessEncoderJob('failed job')).resolves.toEqual({
      job_id: 'replacement',
      path: '/media/a.mkv',
      stage: 'pending',
      created: true,
    })

    expect(new URL(String(fetchMock.mock.calls[0]![0])).pathname).toBe(
      '/api/encoder/jobs/failed%20job/reprocess',
    )
    expect(fetchMock.mock.calls[0]![1].method).toBe('POST')
  })

  it('loads rules and jobs and deletes a job through its no-content endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ rules: [], fallback: 'skip' }))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(noContentResponse())
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchEncoderRules()).resolves.toEqual({ rules: [], fallback: 'skip' })
    await expect(fetchEncoderJobs()).resolves.toEqual([])
    await expect(deleteEncoderJob('job')).resolves.toBeUndefined()
  })
})
