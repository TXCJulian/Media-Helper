import { useCallback, useEffect, useState } from 'react'
import Landing from '@/components/Landing'
import type { PanelName } from '@/components/Landing'
import EpisodePanel from '@/components/EpisodePanel'
import MusicPanel from '@/components/MusicPanel'
import LyricsPanel from '@/components/LyricsPanel'
import CutterPanel from '@/components/CutterPanel'
import { fetchConfig } from '@/lib/api'
import type { CutterPersistedState, CutterSourceState } from '@/types'

const EMPTY_SOURCE_STATE: CutterSourceState = {
  probe: null,
  peaks: [],
  filePath: '',
  fileId: '',
  thumbnailUrl: '',
  files: [],
  jobId: '',
  outputFiles: [],
  isLoadingFile: false,
}

const INITIAL_CUTTER_STATE: CutterPersistedState = {
  form: {
    source: 'server',
    directory: '',
    base: '',
    filename: '',
    inPoint: 0,
    outPoint: 0,
    outputName: '',
    streamCopy: true,
    codec: 'libx264',
    container: 'mp4',
    audioTracks: [],
    keepQuality: false,
  },
  directories: [],
  search: '',
  serverState: { ...EMPTY_SOURCE_STATE },
  uploadState: { ...EMPTY_SOURCE_STATE },
}

export default function App() {
  const [activeView, setActiveView] = useState<'home' | PanelName>('home')
  const [enabledFeatures, setEnabledFeatures] = useState<PanelName[]>([])
  const [basePaths, setBasePaths] = useState<string[]>([])
  const [backendStatus, setBackendStatus] = useState<'checking' | 'connected' | 'unreachable'>(
    'checking',
  )

  useEffect(() => {
    let cancelled = false

    fetchConfig()
      .then((cfg) => {
        if (cancelled) return
        setEnabledFeatures(cfg.features as PanelName[])
        setBasePaths(cfg.base_paths ?? [])
        setBackendStatus('connected')
      })
      .catch((err) => {
        if (cancelled) return
        console.warn('Backend is not reachable', err)
        setEnabledFeatures([])
        setBackendStatus('unreachable')
      })

    return () => {
      cancelled = true
    }
  }, [])

  // Per-panel log + error state
  const [episodeLog, setEpisodeLog] = useState<string[]>([])
  const [episodeError, setEpisodeError] = useState('')
  const [episodeStarted, setEpisodeStarted] = useState(false)

  const [musicLog, setMusicLog] = useState<string[]>([])
  const [musicError, setMusicError] = useState('')
  const [musicStarted, setMusicStarted] = useState(false)

  const [lyricsLog, setLyricsLog] = useState<string[]>([])
  const [lyricsError, setLyricsError] = useState('')
  const [lyricsStarted, setLyricsStarted] = useState(false)

  const [cutterLog, setCutterLog] = useState<string[]>([])
  const [cutterError, setCutterError] = useState('')
  const [cutterStarted, setCutterStarted] = useState(false)
  const [cutterState, setCutterState] = useState<CutterPersistedState>(INITIAL_CUTTER_STATE)

  const handleEpisodeLog = useCallback((log: string[]) => {
    setEpisodeStarted(true)
    setEpisodeLog(log)
  }, [])

  const handleMusicLog = useCallback((log: string[]) => {
    setMusicStarted(true)
    setMusicLog(log)
  }, [])

  const handleLyricsLog = useCallback((log: string[]) => {
    setLyricsStarted(true)
    setLyricsLog(log)
  }, [])

  const handleCutterLog = useCallback((log: string[]) => {
    setCutterStarted(true)
    setCutterLog(log)
  }, [])

  const handleEpisodeError = useCallback((err: string) => setEpisodeError(err), [])
  const handleMusicError = useCallback((err: string) => setMusicError(err), [])
  const handleLyricsError = useCallback((err: string) => setLyricsError(err), [])
  const handleCutterError = useCallback((err: string) => setCutterError(err), [])

  const goHome = useCallback(() => {
    setActiveView('home')
    window.scrollTo(0, 0)
  }, [])

  const showPanel = useCallback((panel: PanelName) => {
    setActiveView(panel)
    window.scrollTo(0, 0)
  }, [])

  if (activeView === 'home') {
    return (
      <Landing
        onNavigate={showPanel}
        enabledFeatures={enabledFeatures}
        backendStatus={backendStatus}
      />
    )
  }

  if (activeView === 'episodes') {
    return (
      <EpisodePanel
        onLog={handleEpisodeLog}
        onError={handleEpisodeError}
        onBack={goHome}
        log={episodeLog}
        error={episodeError}
        hasStarted={episodeStarted}
        showBaseLabel={basePaths.length > 1}
      />
    )
  }

  if (activeView === 'music') {
    return (
      <MusicPanel
        onLog={handleMusicLog}
        onError={handleMusicError}
        onBack={goHome}
        log={musicLog}
        error={musicError}
        hasStarted={musicStarted}
        showBaseLabel={basePaths.length > 1}
      />
    )
  }

  if (activeView === 'lyrics') {
    return (
      <LyricsPanel
        onLog={handleLyricsLog}
        onError={handleLyricsError}
        onBack={goHome}
        log={lyricsLog}
        error={lyricsError}
        hasStarted={lyricsStarted}
        showBaseLabel={basePaths.length > 1}
      />
    )
  }

  if (activeView === 'cutter') {
    return (
      <CutterPanel
        onLog={handleCutterLog}
        onError={handleCutterError}
        onBack={goHome}
        log={cutterLog}
        error={cutterError}
        hasStarted={cutterStarted}
        persisted={cutterState}
        onPersistedChange={setCutterState}
        showBaseLabel={basePaths.length > 1}
      />
    )
  }

  return null
}
