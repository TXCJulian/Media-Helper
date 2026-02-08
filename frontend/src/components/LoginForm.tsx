import { useState } from 'react'

interface LoginFormProps {
  onSubmit: (username: string, password: string) => Promise<void>
  error: string
  title: string
  buttonText: string
}

export default function LoginForm({ onSubmit, error, title, buttonText }: LoginFormProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    await onSubmit(username, password)
    setLoading(false)
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-full max-w-sm rounded-lg bg-[#1e1e1e] p-8 shadow-md">
        <h1 className="mb-6 text-center text-2xl font-bold text-white">{title}</h1>
        {error && (
          <div className="mb-4 rounded bg-red-900/50 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}
        <form onSubmit={(e) => void handleSubmit(e)}>
          <div className="mb-4">
            <label className="mb-1 block text-sm font-semibold text-[#bbb]">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus
              className="h-10 w-full rounded border border-[#333] bg-[#2a2a2a] px-3 text-[#e0e0e0] outline-none focus:border-blue-500"
            />
          </div>
          <div className="mb-6">
            <label className="mb-1 block text-sm font-semibold text-[#bbb]">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
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
            {loading ? '...' : buttonText}
          </button>
        </form>
      </div>
    </div>
  )
}
