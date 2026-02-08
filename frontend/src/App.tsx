import { useCallback, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import LoginForm from '@/components/LoginForm'
import EpisodePanel from '@/components/EpisodePanel'
import MusicPanel from '@/components/MusicPanel'
import LogDisplay from '@/components/LogDisplay'
import UserManagement from '@/components/UserManagement'

export default function App() {
  const { state, username, error: authError, login, register, logout } = useAuth()
  const [episodeLog, setEpisodeLog] = useState<string[]>([])
  const [musicLog, setMusicLog] = useState<string[]>([])
  const [error, setError] = useState('')
  const [hasStartedRename, setHasStartedRename] = useState(false)
  const [showUsers, setShowUsers] = useState(false)

  const handleEpisodeLog = useCallback((log: string[]) => {
    setHasStartedRename(true)
    setEpisodeLog(log)
    setMusicLog([])
  }, [])

  const handleMusicLog = useCallback((log: string[]) => {
    setHasStartedRename(true)
    setMusicLog(log)
    setEpisodeLog([])
  }, [])

  const handleError = useCallback((err: string) => {
    setError(err)
  }, [])

  if (state === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-[#bbb]">Loading...</div>
      </div>
    )
  }

  if (state === 'setup') {
    return (
      <LoginForm
        onSubmit={register}
        error={authError}
        title="Create Admin Account"
        buttonText="Create Account"
      />
    )
  }

  if (state === 'login') {
    return (
      <LoginForm
        onSubmit={login}
        error={authError}
        title="Media Renamer"
        buttonText="Sign In"
      />
    )
  }

  return (
    <div className="mx-auto w-full max-w-[1000px]">
      <div className="mb-12 mt-0 flex items-center justify-between">
        <h1 className="text-3xl font-bold text-white">Media Renamer</h1>
        <div className="flex items-center gap-3">
          <span className="text-sm text-[#bbb]">{username}</span>
          <button
            onClick={() => setShowUsers(true)}
            className="rounded bg-[#333] px-3 py-1.5 text-sm text-[#e0e0e0] hover:bg-[#444]"
          >
            Users
          </button>
          <button
            onClick={logout}
            className="rounded bg-[#333] px-3 py-1.5 text-sm text-[#e0e0e0] hover:bg-[#444]"
          >
            Logout
          </button>
        </div>
      </div>

      {showUsers && <UserManagement onClose={() => setShowUsers(false)} />}

      <div className="grid grid-cols-1 items-stretch gap-6 md:grid-cols-2">
        <EpisodePanel onLog={handleEpisodeLog} onError={handleError} />
        <MusicPanel onLog={handleMusicLog} onError={handleError} />
        <LogDisplay
          episodeLog={episodeLog}
          musicLog={musicLog}
          error={error}
          hasStartedRename={hasStartedRename}
        />
      </div>
    </div>
  )
}
