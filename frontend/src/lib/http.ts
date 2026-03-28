export const API_BASE = window.location.origin

export function assertAuthenticated(res: Response | XMLHttpRequest): void {
  if (res.status === 401) {
    window.dispatchEvent(new Event('auth:expired'))
    throw new Error('Session expired')
  }
}
