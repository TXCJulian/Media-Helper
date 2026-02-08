import { useCallback, useEffect, useState } from 'react'
import { fetchJson, postForm } from '@/lib/api'
import type { User, UsersResponse } from '@/types'

interface UserManagementProps {
  onClose: () => void
}

export default function UserManagement({ onClose }: UserManagementProps) {
  const [users, setUsers] = useState<User[]>([])
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const loadUsers = useCallback(async () => {
    try {
      const data = await fetchJson<UsersResponse>('/auth/users')
      setUsers(data.users)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users')
    }
  }, [])

  useEffect(() => {
    void loadUsers()
  }, [loadUsers])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    setSuccess('')
    try {
      await postForm<{ status: string; username: string }>('/auth/register-user', {
        username,
        password,
      })
      setSuccess(`User "${username}" angelegt`)
      setUsername('')
      setPassword('')
      await loadUsers()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create user')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-md rounded-lg bg-[#1e1e1e] p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-white">User Management</h2>
          <button
            onClick={onClose}
            className="rounded px-2 py-1 text-[#bbb] hover:bg-[#333] hover:text-white"
          >
            ✕
          </button>
        </div>

        {error && (
          <div className="mb-3 rounded bg-red-900/50 px-3 py-2 text-sm text-red-300">{error}</div>
        )}
        {success && (
          <div className="mb-3 rounded bg-green-900/50 px-3 py-2 text-sm text-green-300">
            {success}
          </div>
        )}

        <div className="mb-5">
          <h3 className="mb-2 text-sm font-semibold text-[#bbb]">Existing Users</h3>
          {loading ? (
            <p className="text-sm text-[#666]">Loading...</p>
          ) : users.length === 0 ? (
            <p className="text-sm text-[#666]">No users</p>
          ) : (
            <ul className="space-y-1">
              {users.map((u) => (
                <li
                  key={u.id}
                  className="flex items-center justify-between rounded bg-[#2a2a2a] px-3 py-2 text-sm text-[#e0e0e0]"
                >
                  <span>{u.username}</span>
                  <span className="text-xs text-[#666]">{u.created_at}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <form onSubmit={(e) => void handleSubmit(e)}>
          <h3 className="mb-2 text-sm font-semibold text-[#bbb]">Add New User</h3>
          <div className="mb-3">
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Username"
              required
              className="h-10 w-full rounded border border-[#333] bg-[#2a2a2a] px-3 text-[#e0e0e0] outline-none focus:border-blue-500"
            />
          </div>
          <div className="mb-4">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              required
              minLength={4}
              className="h-10 w-full rounded border border-[#333] bg-[#2a2a2a] px-3 text-[#e0e0e0] outline-none focus:border-blue-500"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="h-10 w-full rounded bg-blue-600 font-semibold text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? '...' : 'User anlegen'}
          </button>
        </form>
      </div>
    </div>
  )
}
