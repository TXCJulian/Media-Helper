export const API_BASE = window.location.origin

const DEFAULT_TIMEOUT_MS = 30_000

async function extractErrorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (body.detail) return String(body.detail)
    if (body.error) return String(body.error)
  } catch {
    // Response body is not JSON
  }
  return `HTTP ${res.status}: ${res.statusText}`
}

export async function fetchJson<T>(
  path: string,
  params?: Record<string, string>,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const url = new URL(path, API_BASE)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v) url.searchParams.set(k, v)
    }
  }
  const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res))
  }
  return res.json() as Promise<T>
}

export async function postForm<T>(
  path: string,
  data: Record<string, string | number | boolean>,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const formData = new FormData()
  for (const [k, v] of Object.entries(data)) {
    formData.append(k, String(v))
  }
  const url = new URL(path, API_BASE)
  const res = await fetch(url, {
    method: 'POST',
    body: formData,
    signal: AbortSignal.timeout(timeoutMs),
  })
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res))
  }
  return res.json() as Promise<T>
}

export async function postRefresh(): Promise<void> {
  await postForm('/directories/refresh', {})
}

export async function fetchTranscriberHealth(): Promise<import('@/types').TranscriberHealth> {
  return fetchJson('/transcribe/health')
}

export async function fetchMusicFiles(
  directory: string,
): Promise<import('@/types').MusicFilesResponse> {
  return fetchJson('/transcribe/files', { directory })
}

export async function fetchConfig(): Promise<{ features: string[] }> {
  return fetchJson('/config')
}

export function uploadFile(
  file: File,
  onProgress?: (pct: number) => void,
): Promise<{
  job_id: string
  file_id: string
  filename: string
  probe: import('@/types').ProbeResult
}> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', new URL('/cutter/upload', API_BASE).href)

    if (onProgress) {
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      })
    }

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch {
          reject(new Error('Invalid JSON response'))
        }
      } else {
        try {
          const body = JSON.parse(xhr.responseText)
          reject(new Error(body.detail ?? body.error ?? `HTTP ${xhr.status}`))
        } catch {
          reject(new Error(`HTTP ${xhr.status}: ${xhr.statusText}`))
        }
      }
    })

    xhr.addEventListener('error', () => reject(new Error('Upload failed')))
    xhr.addEventListener('abort', () => reject(new Error('Upload aborted')))

    const formData = new FormData()
    formData.append('file', file)
    xhr.send(formData)
  })
}

export function fetchProbe(
  path: string,
  source: string,
  jobId = '',
  timeoutMs = 60_000,
): Promise<import('@/types').ProbeResult> {
  const params: Record<string, string> = { path, source }
  if (jobId) params.job_id = jobId
  return fetchJson<import('@/types').ProbeResult>('/cutter/probe', params, timeoutMs)
}

export function fetchWaveform(
  path: string,
  source: string,
  peaks?: number,
  jobId = '',
  timeoutMs = 120_000,
): Promise<{ peaks: number[] }> {
  const params: Record<string, string> = { path, source }
  if (peaks) params.peaks = String(peaks)
  if (jobId) params.job_id = jobId
  return fetchJson<{ peaks: number[] }>('/cutter/waveform', params, timeoutMs)
}

export function fetchCutterFiles(
  directory: string,
): Promise<{ files: import('@/types').CutterFileInfo[] }> {
  return fetchJson<{ files: import('@/types').CutterFileInfo[] }>('/cutter/files', { directory })
}

export function createJob(
  path: string,
  source = 'server',
): Promise<{ job_id: string }> {
  return postForm<{ job_id: string }>('/cutter/jobs', { path, source })
}

export function getStreamUrl(
  fileId: string,
  audioStreamIndex?: number | null,
  transcode = false,
): string {
  const base = `/cutter/stream/${encodeURIComponent(fileId)}`
  const params = new URLSearchParams()
  if (audioStreamIndex != null) params.set('audio_stream', String(audioStreamIndex))
  if (transcode) params.set('transcode', 'true')
  const query = params.toString()
  return query ? `${base}?${query}` : base
}

export function fetchPreviewStatus(
  fileId: string,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<import('@/types').CutterPreviewStatus> {
  return fetchJson<import('@/types').CutterPreviewStatus>(
    `/cutter/preview-status/${encodeURIComponent(fileId)}`,
    undefined,
    timeoutMs,
  )
}

export function getThumbnailUrl(path: string, source: string, jobId = '', count = 30): string {
  const params = new URLSearchParams({ path, source, count: String(count) })
  if (jobId) params.set('job_id', jobId)
  return `/cutter/thumbnails?${params.toString()}`
}

export function getDownloadUrl(jobId: string, filename: string): string {
  return `/cutter/jobs/${encodeURIComponent(jobId)}/download/${encodeURIComponent(filename)}`
}

export async function listJobs(): Promise<{ jobs: import('@/types').CutterJob[] }> {
  return fetchJson<{ jobs: import('@/types').CutterJob[] }>('/cutter/jobs')
}

export async function deleteJob(jobId: string): Promise<void> {
  const url = new URL(`/cutter/jobs/${encodeURIComponent(jobId)}`, API_BASE)
  const res = await fetch(url, { method: 'DELETE', signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS) })
  if (!res.ok) throw new Error(await extractErrorMessage(res))
}

export async function getJob(jobId: string): Promise<import('@/types').CutterJob> {
  return fetchJson<import('@/types').CutterJob>(`/cutter/jobs/${encodeURIComponent(jobId)}`)
}

export async function saveToSource(jobId: string, filename: string): Promise<{ status: string; filename: string }> {
  const url = new URL(`/cutter/jobs/${encodeURIComponent(jobId)}/save/${encodeURIComponent(filename)}`, API_BASE)
  const res = await fetch(url, { method: 'POST', signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS) })
  if (!res.ok) throw new Error(await extractErrorMessage(res))
  return res.json()
}
