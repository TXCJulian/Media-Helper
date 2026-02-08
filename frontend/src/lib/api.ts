const envBase = import.meta.env.VITE_API_BASE_URL as string | undefined
export const API_BASE = envBase && envBase.trim() ? envBase.trim() : window.location.origin

export async function fetchJson<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(path, API_BASE)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v) url.searchParams.set(k, v)
    }
  }
  const res = await fetch(url)
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export async function postForm<T>(
  path: string,
  data: Record<string, string | number | boolean>,
): Promise<T> {
  const formData = new FormData()
  for (const [k, v] of Object.entries(data)) {
    formData.append(k, String(v))
  }
  const url = new URL(path, API_BASE)
  const res = await fetch(url, { method: 'POST', body: formData })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export async function postRefresh(): Promise<void> {
  const url = new URL('/directories/refresh', API_BASE)
  const res = await fetch(url, { method: 'POST' })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  }
}
