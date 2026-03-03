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
