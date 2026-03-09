import PanelLayout from '@/components/PanelLayout'

interface CutterPanelProps {
  onLog: (log: string[]) => void
  onError: (err: string) => void
  onBack: () => void
  log: string[]
  error: string
  hasStarted: boolean
}

export default function CutterPanel({ onBack }: CutterPanelProps) {
  return (
    <PanelLayout title="Media Cutter" onBack={onBack}>
      <div className="flex flex-col items-center justify-center gap-4 py-16 text-center">
        <span className="text-[2rem]">✂</span>
        <p className="text-[var(--text-secondary)]">
          Media Cutter is coming soon. Trim audio and video files with waveform preview.
        </p>
      </div>
    </PanelLayout>
  )
}
