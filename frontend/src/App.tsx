import { useCallback, useState } from 'react'
import EpisodePanel from '@/components/EpisodePanel'
import MusicPanel from '@/components/MusicPanel'
import LogDisplay from '@/components/LogDisplay'

export default function App() {
  const [episodeLog, setEpisodeLog] = useState<string[]>([])
  const [musicLog, setMusicLog] = useState<string[]>([])
  const [error, setError] = useState('')
  const [hasStartedRename, setHasStartedRename] = useState(false)

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

  return (
    <div className="mx-auto w-full max-w-[1000px]">
      <h1 className="mb-12 mt-0 text-center text-3xl font-bold text-white">Media Renamer</h1>

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
