interface LogDisplayProps {
  episodeLog: string[]
  musicLog: string[]
  error: string
  hasStartedRename: boolean
}

export default function LogDisplay({
  episodeLog,
  musicLog,
  error,
  hasStartedRename,
}: LogDisplayProps) {
  const lines: string[] = []
  if (episodeLog.length > 0) lines.push(...episodeLog)
  if (musicLog.length > 0) {
    if (lines.length > 0) lines.push('')
    lines.push(...musicLog)
  }

  const text =
    lines.length > 0 ? lines.join('\n') : hasStartedRename ? '' : 'Bereit für Umbenennung...'

  const displayText = error ? `Fehler: ${error}` : text
  const displayClass = error ? 'text-[#b00020]' : 'text-green-400'

  return (
    <div className="col-span-full">
      <div className="overflow-auto rounded-md bg-[#111]">
        <pre className="m-0 overflow-y-auto rounded-lg border-none bg-[#1e1e1e] p-4 font-mono">
          <span className={displayClass}>{displayText}</span>
        </pre>
      </div>
    </div>
  )
}
