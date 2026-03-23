import { useState } from 'react'
import type { FormEvent } from 'react'
import { API_BASE } from '@/lib/api'

interface LoginProps {
  onSuccess: () => void
}

export default function Login({ onSuccess }: LoginProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const res = await fetch(new URL('/auth/login', API_BASE), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username, password }),
        signal: AbortSignal.timeout(30_000),
      })

      if (!res.ok) {
        const body = await res.json().catch(() => null)
        setError(body?.detail ?? `Login failed (HTTP ${res.status})`)
        return
      }

      onSuccess()
    } catch {
      setError('Connection failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="glass-strong w-full max-w-sm p-8">
        <h1 className="mb-6 text-center text-xl font-semibold tracking-tight">Media Renamer</h1>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <input
            type="text"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            className="w-full rounded-lg border border-[var(--glass-border)] bg-[var(--bg-glass)] px-4 py-2.5 text-sm text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--accent)]"
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            className="w-full rounded-lg border border-[var(--glass-border)] bg-[var(--bg-glass)] px-4 py-2.5 text-sm text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--accent)]"
          />

          {error && <p className="text-center text-sm text-[var(--error)]">{error}</p>}

          <button
            type="submit"
            disabled={loading || !username || !password}
            className="mt-1 w-full cursor-pointer rounded-lg bg-[var(--accent)] px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
