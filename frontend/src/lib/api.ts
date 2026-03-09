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
): Promise<{ file_id: string; filename: string; probe: import('@/types').ProbeResult }> {
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
): Promise<import('@/types').ProbeResult> {
  return fetchJson<import('@/types').ProbeResult>('/cutter/probe', { path, source })
}

export function fetchWaveform(
  path: string,
  source: string,
  peaks?: number,
): Promise<{ peaks: number[] }> {
  const params: Record<string, string> = { path, source }
  if (peaks) params.peaks = String(peaks)
  return fetchJson<{ peaks: number[] }>('/cutter/waveform', params)
}

export function fetchCutterFiles(
  directory: string,
): Promise<{ files: import('@/types').CutterFileInfo[] }> {
  return fetchJson<{ files: import('@/types').CutterFileInfo[] }>('/cutter/files', { directory })
}

export function getStreamUrl(fileId: string): string {
  return `/cutter/stream/${encodeURIComponent(fileId)}`
}
