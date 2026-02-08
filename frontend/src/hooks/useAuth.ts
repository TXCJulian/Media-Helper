import { useCallback, useEffect, useState } from 'react'
import { fetchJson, postForm, setToken, getToken } from '@/lib/api'
import type { AuthStatus, AuthResponse } from '@/types'

export type AuthState = 'loading' | 'setup' | 'login' | 'authenticated'

export function useAuth() {
  const [state, setState] = useState<AuthState>('loading')
  const [username, setUsername] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    async function check() {
      const token = getToken()
      if (token) {
        try {
          const me = await fetchJson<{ username: string }>('/auth/me')
          setUsername(me.username)
          setState('authenticated')
          return
        } catch {
          setToken(null)
        }
      }
      try {
        const status = await fetchJson<AuthStatus>('/auth/status')
        setState(status.setup_required ? 'setup' : 'login')
      } catch {
        setState('login')
      }
    }
    void check()
  }, [])

  const login = useCallback(async (user: string, password: string) => {
    setError('')
    try {
      const data = await postForm<AuthResponse>('/auth/login', {
        username: user,
        password,
      })
      setToken(data.token)
      setUsername(data.username)
      setState('authenticated')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    }
  }, [])

  const register = useCallback(async (user: string, password: string) => {
    setError('')
    try {
      const data = await postForm<AuthResponse>('/auth/register', {
        username: user,
        password,
      })
      setToken(data.token)
      setUsername(data.username)
      setState('authenticated')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    }
  }, [])

  const logout = useCallback(() => {
    setToken(null)
    setUsername(null)
    setState('login')
  }, [])

  return { state, username, error, login, register, logout }
}
