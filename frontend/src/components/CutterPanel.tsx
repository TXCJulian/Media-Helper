import { useCallback, useEffect, useRef, useState } from 'react'
import PanelLayout from '@/components/PanelLayout'
import LogPanel from '@/components/LogPanel'
import MediaPlayer from '@/components/cutter/MediaPlayer'
import TrimControls from '@/components/cutter/TrimControls'
import OutputSettings from '@/components/cutter/OutputSettings'
import SegmentedControl from '@/components/ui/SegmentedControl'
import FormSection from '@/components/ui/FormSection'
import { connectSSE } from '@/lib/sse'
import {
  fetchJson,
  uploadFile,
  fetchProbe,
  fetchWaveform,
  fetchCutterFiles,
  getStreamUrl,
} from '@/lib/api'
import { useDebounce } from '@/hooks/useDebounce'
import type { CutterForm, ProbeResult, CutterFileInfo, DirectoriesResponse } from '@/types'

interface CutterPanelProps {
  onLog: (log: string[]) => void
  onError: (error: string) => void
  onBack: () => void
  log: string[]
  error: string
  hasStarted: boolean
}

export default function CutterPanel({
  onLog,
  onError,
  onBack,
  log,
  error,
  hasStarted,
}: CutterPanelProps) {
  const [form, setForm] = useState<CutterForm>({
    source: 'server',
    directory: '',
    filename: '',
    inPoint: 0,
    outPoint: 0,
    outputName: '',
    streamCopy: true,
    codec: 'aac',
    container: 'mp4',
  })

  const [directories, setDirectories] = useState<string[]>([])
  const [isLoadingDirs, setIsLoadingDirs] = useState(false)
  const [files, setFiles] = useState<CutterFileInfo[]>([])
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const [peaks, setPeaks] = useState<number[]>([])
  const [fileId, setFileId] = useState('')
  const [isCutting, setIsCutting] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(-1) // -1 = not uploading
  const [isDragOver, setIsDragOver] = useState(false)

  const abortSSERef = useRef<(() => void) | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const debouncedDirectory = useDebounce(form.directory, 500)

  const update = <K extends keyof CutterForm>(key: K, value: CutterForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  // ── Fetch directories on mount ────────────────────────────────
  const fetchDirs = useCallback(async () => {
    setIsLoadingDirs(true)
    onError('')
    try {
      const data = await fetchJson<DirectoriesResponse>('/directories/music')
      const dirs = data.directories ?? []
      setDirectories(dirs)
    } catch (err) {
      onError(`Error loading directories: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setIsLoadingDirs(false)
    }
  }, [onError])

  useEffect(() => {
    void fetchDirs()
  }, [fetchDirs])

  // ── Fetch files when directory changes ────────────────────────
  useEffect(() => {
    if (!debouncedDirectory) {
      setFiles([])
      return
    }
    const signal = { cancelled: false }
    setIsLoadingFiles(true)
    fetchCutterFiles(debouncedDirectory)
      .then((data) => {
        if (signal.cancelled) return
        setFiles(data.files ?? [])
      })
      .catch(() => {
        if (signal.cancelled) return
        setFiles([])
      })
      .finally(() => {
        if (!signal.cancelled) setIsLoadingFiles(false)
      })
    return () => {
      signal.cancelled = true
    }
  }, [debouncedDirectory])

  // ── Load probe + waveform for a file ──────────────────────────
  const loadFileData = useCallback(
    async (path: string, source: 'server' | 'upload') => {
      onError('')
      setProbe(null)
      setPeaks([])
      try {
        const [probeData, waveData] = await Promise.all([
          fetchProbe(path, source),
          fetchWaveform(path, source, 200),
        ])
        setProbe(probeData)
        setPeaks(waveData.peaks)
        setForm((prev) => ({
          ...prev,
          inPoint: 0,
          outPoint: probeData.duration,
        }))
      } catch (err) {
        onError(`Error loading file: ${err instanceof Error ? err.message : String(err)}`)
      }
    },
    [onError],
  )

  // ── Server file selection ─────────────────────────────────────
  const handleFileSelect = useCallback(
    (file: CutterFileInfo) => {
      const path = `${form.directory}/${file.name}`
      setForm((prev) => ({ ...prev, filename: file.name }))
      setFileId(path)
      void loadFileData(path, 'server')
    },
    [form.directory, loadFileData],
  )

  // ── Directory selection from dropdown ─────────────────────────
  const handleDirectoryChange = (dir: string) => {
    update('directory', dir)
    update('filename', '')
    setProbe(null)
    setPeaks([])
    setFileId('')
  }

  // ── Upload handling ───────────────────────────────────────────
  const handleUpload = useCallback(
    async (file: File) => {
      setUploadProgress(0)
      onError('')
      setProbe(null)
      setPeaks([])
      setFileId('')
      try {
        const result = await uploadFile(file, setUploadProgress)
        setProbe(result.probe)
        setFileId(result.file_id)
        setForm((prev) => ({
          ...prev,
          filename: result.filename,
          inPoint: 0,
          outPoint: result.probe.duration,
        }))
        // Fetch waveform for uploaded file
        const waveData = await fetchWaveform(result.file_id, 'upload', 200)
        setPeaks(waveData.peaks)
      } catch (err) {
        onError(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
      } finally {
        setUploadProgress(-1)
      }
    },
    [onError],
  )

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setIsDragOver(false)
      const file = e.dataTransfer.files[0]
      if (file) void handleUpload(file)
    },
    [handleUpload],
  )

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) void handleUpload(file)
      // Reset input so same file can be re-selected
      e.target.value = ''
    },
    [handleUpload],
  )

  // ── Cut execution ─────────────────────────────────────────────
  const handleCut = (e: React.FormEvent) => {
    e.preventDefault()
    if (!fileId || !probe || isCutting) return

    setIsCutting(true)
    onError('')
    onLog([])

    const logs: string[] = []

    const params: Record<string, string> = {
      file_id: fileId,
      source: form.source,
      in_point: String(form.inPoint),
      out_point: String(form.outPoint),
      stream_copy: String(form.streamCopy),
    }
    if (form.outputName) params.output_name = form.outputName
    if (!form.streamCopy) {
      params.codec = form.codec
      params.container = form.container
    }

    abortSSERef.current?.()
    abortSSERef.current = connectSSE('/cutter/cut', params, {
      onProgress: (data) => {
        if (data === 'heartbeat') return
        logs.push(data)
        onLog([...logs])
      },
      onError: (data) => {
        logs.push(`[ERROR]\t\t\t${data}`)
        onLog([...logs])
        setIsCutting(false)
      },
      onDone: (data) => {
        logs.push(`\n${data}`)
        onLog([...logs])
        setIsCutting(false)
        abortSSERef.current = null
      },
    })
  }

  // Abort SSE on unmount
  useEffect(() => {
    return () => {
      abortSSERef.current?.()
    }
  }, [])

  // Reset file state when switching source tabs
  const handleSourceChange = (source: string) => {
    update('source', source as CutterForm['source'])
    update('filename', '')
    setProbe(null)
    setPeaks([])
    setFileId('')
    setFiles([])
    setUploadProgress(-1)
  }

  const busy = isLoadingDirs || isCutting
  const isVideo = probe?.video_codec != null
  const hasFile = !!probe && !!fileId

  return (
    <PanelLayout title="Media Cutter" onBack={onBack}>
      <form onSubmit={handleCut}>
        {/* Source selector */}
        <FormSection label="Source">
          <SegmentedControl
            options={[
              { label: 'Server', value: 'server' },
              { label: 'Upload', value: 'upload' },
            ]}
            value={form.source}
            onChange={handleSourceChange}
            disabled={isCutting}
            color="emerald"
          />
        </FormSection>

        {/* ── Server tab ────────────────────────────────────── */}
        {form.source === 'server' && (
          <>
            <FormSection label="Directory">
              <input
                type="text"
                value={form.directory}
                onChange={(e) => handleDirectoryChange(e.target.value)}
                placeholder="Type a directory path..."
                className="input-field input-emerald"
                disabled={isCutting}
                list="cutter-dirs"
              />
              <datalist id="cutter-dirs">
                {directories.map((dir) => (
                  <option key={dir} value={dir} />
                ))}
              </datalist>
            </FormSection>

            {/* File list */}
            {form.directory && (
              <FormSection label="Files">
                <div className="max-h-[260px] overflow-y-auto rounded-[10px] border border-[var(--border)] bg-[var(--bg-input)] p-[0.4rem]">
                  {isLoadingFiles ? (
                    <div className="py-4 text-center text-[0.8rem] text-[var(--text-tertiary)]">
                      Loading files...
                    </div>
                  ) : files.length === 0 ? (
                    <div className="py-4 text-center text-[0.8rem] text-[var(--text-tertiary)]">
                      No media files found
                    </div>
                  ) : (
                    files.map((file) => {
                      const isSelected = form.filename === file.name
                      return (
                        <button
                          key={file.name}
                          type="button"
                          onClick={() => handleFileSelect(file)}
                          disabled={isCutting}
                          className={`flex w-full cursor-pointer items-center gap-[0.6rem] rounded-lg border-none bg-transparent px-3 py-[0.5rem] text-left font-[Geist,sans-serif] transition-colors duration-150 hover:bg-[rgba(255,255,255,0.025)] ${
                            isSelected
                              ? 'bg-[rgba(52,211,153,0.08)] text-[var(--accent-4)]'
                              : 'text-[var(--text-primary)]'
                          } ${isCutting ? 'cursor-not-allowed opacity-50' : ''}`}
                        >
                          <span className="min-w-0 flex-1 truncate text-[0.8rem]">{file.name}</span>
                          <span className="shrink-0 text-[0.68rem] text-[var(--text-tertiary)]">
                            {formatFileSize(file.size)}
                          </span>
                          <span className="shrink-0 rounded-[5px] bg-[rgba(52,211,153,0.1)] px-[0.4rem] py-[0.12rem] text-[0.56rem] font-semibold uppercase tracking-[0.04em] text-[var(--accent-4)]">
                            {file.extension}
                          </span>
                        </button>
                      )
                    })
                  )}
                </div>
              </FormSection>
            )}
          </>
        )}

        {/* ── Upload tab ────────────────────────────────────── */}
        {form.source === 'upload' && (
          <FormSection label="Upload File">
            {uploadProgress >= 0 ? (
              <div className="rounded-[10px] border border-[var(--border)] bg-[var(--bg-input)] p-6">
                <div className="mb-2 text-center text-[0.8rem] text-[var(--text-secondary)]">
                  Uploading... {uploadProgress}%
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-[rgba(255,255,255,0.05)]">
                  <div
                    className="h-full rounded-full bg-[var(--accent-4)] transition-all duration-300"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
              </div>
            ) : (
              <div
                onDragOver={(e) => {
                  e.preventDefault()
                  setIsDragOver(true)
                }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-[10px] border-2 border-dashed py-12 transition-all duration-200 ${
                  isDragOver
                    ? 'border-[var(--accent-4)] bg-[rgba(52,211,153,0.06)]'
                    : 'border-[var(--border)] bg-[var(--bg-input)] hover:border-[var(--glass-border-hover)] hover:bg-[rgba(255,255,255,0.015)]'
                }`}
              >
                <span className="text-[1.5rem] text-[var(--text-tertiary)]">
                  {isDragOver ? '\u2B07' : '\uD83D\uDCC1'}
                </span>
                <span className="text-[0.85rem] text-[var(--text-secondary)]">
                  {isDragOver ? 'Drop file here' : 'Drag & drop a file, or click to browse'}
                </span>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="audio/*,video/*"
                  onChange={handleFileInputChange}
                  className="hidden"
                />
              </div>
            )}
            {/* Show selected filename after upload */}
            {form.filename && probe && uploadProgress < 0 && (
              <div className="mt-3 flex items-center gap-2 rounded-lg bg-[rgba(52,211,153,0.08)] px-3 py-2">
                <span className="text-[0.8rem] text-[var(--accent-4)]">{form.filename}</span>
              </div>
            )}
          </FormSection>
        )}

        {/* ── Player section (shown after file is loaded) ─── */}
        {hasFile && (
          <>
            <FormSection label="Preview">
              <MediaPlayer
                streamUrl={getStreamUrl(fileId)}
                isVideo={isVideo}
                peaks={peaks}
                duration={probe.duration}
                inPoint={form.inPoint}
                outPoint={form.outPoint}
                onInPointChange={(t) => update('inPoint', t)}
                onOutPointChange={(t) => update('outPoint', t)}
              />
            </FormSection>

            <FormSection label="Trim">
              <TrimControls
                inPoint={form.inPoint}
                outPoint={form.outPoint}
                duration={probe.duration}
                onInPointChange={(t) => update('inPoint', t)}
                onOutPointChange={(t) => update('outPoint', t)}
              />
            </FormSection>

            <OutputSettings
              outputName={form.outputName}
              streamCopy={form.streamCopy}
              codec={form.codec}
              container={form.container}
              onOutputNameChange={(v) => update('outputName', v)}
              onStreamCopyChange={(v) => update('streamCopy', v)}
              onCodecChange={(v) => update('codec', v)}
              onContainerChange={(v) => update('container', v)}
            />

            <button
              type="submit"
              disabled={busy || !hasFile}
              className="btn-submit btn-emerald"
            >
              {isCutting ? <span className="spinner-md" /> : 'Cut'}
            </button>
          </>
        )}

        <LogPanel
          log={log}
          error={error}
          hasStarted={hasStarted}
          color="emerald"
          idleMessage="Ready to cut..."
        />
      </form>
    </PanelLayout>
  )
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
