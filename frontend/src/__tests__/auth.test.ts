import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

const { fetchAuthStatus, postLogout, fetchJson } = await import('@/lib/api')

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

describe('fetchAuthStatus', () => {
  it('returns auth status', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ auth_enabled: true, authenticated: false }))
    const result = await fetchAuthStatus()
    expect(result.auth_enabled).toBe(true)
    expect(result.authenticated).toBe(false)
  })

  it('includes credentials in request', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ auth_enabled: false, authenticated: true }))
    await fetchAuthStatus()
    const [, options] = mockFetch.mock.calls[0]!
    expect(options.credentials).toBe('include')
  })
})

describe('postLogout', () => {
  it('sends POST to /auth/logout with credentials', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }))
    await postLogout()
    const [url, options] = mockFetch.mock.calls[0]!
    expect(url.toString()).toContain('/auth/logout')
    expect(options.method).toBe('POST')
    expect(options.credentials).toBe('include')
  })
})

describe('401 interceptor', () => {
  it('dispatches auth:expired event on 401', async () => {
    const handler = vi.fn()
    window.addEventListener('auth:expired', handler)

    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Auth required' }), {
        status: 401,
        statusText: 'Unauthorized',
      }),
    )

    await expect(fetchJson('/test')).rejects.toThrow('Session expired')
    expect(handler).toHaveBeenCalled()

    window.removeEventListener('auth:expired', handler)
  })
})
