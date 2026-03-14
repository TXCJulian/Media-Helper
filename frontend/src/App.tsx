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
    filename: '',
    inPoint: 0,
    outPoint: 0,
    outputName: '',
    streamCopy: true,
    codec: 'aac',
    audioCodec: 'copy',
    container: 'mp4',
    audioStreamIndex: null,
  },
  directories: [],
  search: '',
  serverState: { ...EMPTY_SOURCE_STATE },
  uploadState: { ...EMPTY_SOURCE_STATE },
}

export default function App() {
  const [activeView, setActiveView] = useState<'home' | PanelName>('home')
  const [enabledFeatures, setEnabledFeatures] = useState<PanelName[]>([])

  useEffect(() => {
    fetchConfig()
      .then((cfg) => setEnabledFeatures(cfg.features as PanelName[]))
      .catch((err) => {
        console.warn('Failed to fetch enabled features config, falling back to defaults', err)
        setEnabledFeatures(['episodes', 'music'])
      })
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
    return <Landing onNavigate={showPanel} enabledFeatures={enabledFeatures} />
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
      />
    )
  }

  return null
}
