import { useRef, useState } from 'react'
import { deleteEncoderPreset, importEncoderPresets, saveEncoderPreset } from '@/lib/api'
import type { EncoderPreset } from '@/types'

type GuidedPresetFields = {
  name: string
  encoder: string
  videoPreset: string
  fileFormat: string
  qualityType: string
  quality: string
}

type Draft = {
  originalName: string | null
  body: Record<string, unknown>
  rawText: string
  rawError: string | null
}

export type PresetEditorProps = {
  presets: EncoderPreset[]
  onSaved: (preset?: EncoderPreset) => void
  onDeleted: (name?: string) => void
  onError: (message: string) => void
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function cloneBody(body: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(body)) as Record<string, unknown>
}

function asString(value: unknown): string {
  return value == null ? '' : String(value)
}

function patchGuidedLeaf(
  body: Record<string, unknown>,
  form: GuidedPresetFields,
): Record<string, unknown> {
  return {
    ...body,
    PresetName: form.name,
    VideoEncoder: form.encoder,
    VideoPreset: form.videoPreset,
    FileFormat: form.fileFormat,
    VideoQualityType: preserveNumericType(form.qualityType, body.VideoQualityType),
    VideoQualitySlider: preserveNumericType(form.quality, body.VideoQualitySlider),
  }
}

function preserveNumericType(value: string, previous: unknown): string | number {
  if (typeof previous !== 'number') return value
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : value
}

function guidedFields(body: Record<string, unknown>): GuidedPresetFields {
  return {
    name: asString(body.PresetName),
    encoder: asString(body.VideoEncoder),
    videoPreset: asString(body.VideoPreset),
    fileFormat: asString(body.FileFormat),
    qualityType: asString(body.VideoQualityType),
    quality: asString(body.VideoQualitySlider),
  }
}

function formatBody(body: Record<string, unknown>): string {
  return JSON.stringify(body, null, 2)
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export default function PresetEditor({ presets, onSaved, onDeleted, onError }: PresetEditorProps) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [view, setView] = useState<'guided' | 'raw'>('guided')
  const [saving, setSaving] = useState(false)
  const importInput = useRef<HTMLInputElement>(null)
  const seed = presets.find((preset) => preset.body)

  const openDraft = (
    body: Record<string, unknown>,
    originalName: string | null,
    initialView: 'guided' | 'raw' = 'guided',
  ) => {
    const nextBody = cloneBody(body)
    setDraft({ originalName, body: nextBody, rawText: formatBody(nextBody), rawError: null })
    setView(initialView)
  }

  const updateGuided = (field: keyof GuidedPresetFields, value: string) => {
    if (!draft) return
    if (field === 'name' && draft.originalName) return
    const form = { ...guidedFields(draft.body), [field]: value }
    const body = patchGuidedLeaf(draft.body, form)
    setDraft({ ...draft, body, rawText: formatBody(body), rawError: null })
  }

  const updateRaw = (rawText: string) => {
    if (!draft) return
    try {
      const parsed: unknown = JSON.parse(rawText)
      if (!isJsonObject(parsed)) throw new Error('Preset JSON must be an object.')
      const body = draft.originalName ? { ...parsed, PresetName: draft.originalName } : parsed
      setDraft({
        ...draft,
        body,
        rawText: draft.originalName ? formatBody(body) : rawText,
        rawError: null,
      })
    } catch (error) {
      setDraft({ ...draft, rawText, rawError: errorMessage(error) })
    }
  }

  const save = async () => {
    if (!draft || draft.rawError) return
    const name = draft.originalName ?? asString(draft.body.PresetName).trim()
    if (!name) {
      onError('Preset JSON must include a PresetName.')
      return
    }

    setSaving(true)
    try {
      const saved = await saveEncoderPreset(name, draft.body)
      onSaved({
        name,
        encoder: asString(saved.body.VideoEncoder),
        video_preset: asString(saved.body.VideoPreset),
        file_format: asString(saved.body.FileFormat),
        body: saved.body,
      })
      setDraft({ ...draft, originalName: name, body: saved.body, rawText: formatBody(saved.body) })
    } catch (error) {
      onError(errorMessage(error))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (name: string) => {
    try {
      await deleteEncoderPreset(name)
      onDeleted(name)
      if (draft?.originalName === name) setDraft(null)
    } catch (error) {
      onError(errorMessage(error))
    }
  }

  const importDocument = async (file: File) => {
    try {
      const parsed: unknown = JSON.parse(await file.text())
      if (!isJsonObject(parsed)) throw new Error('Imported preset document must be a JSON object.')
      await importEncoderPresets(parsed)
      onSaved()
    } catch (error) {
      onError(errorMessage(error))
    }
  }

  const form = draft ? guidedFields(draft.body) : null

  return (
    <section aria-label="Preset editor" className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => importInput.current?.click()}>
          Import presets
        </button>
        <input
          ref={importInput}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void importDocument(file)
            event.target.value = ''
          }}
        />
        <button
          type="button"
          disabled={!seed?.body}
          onClick={() => {
            if (seed?.body) openDraft({ ...cloneBody(seed.body), PresetName: 'New preset' }, null)
          }}
        >
          New guided preset
        </button>
        <button type="button" onClick={() => openDraft({ PresetName: 'New preset' }, null, 'raw')}>
          New raw preset
        </button>
      </div>

      {!seed?.body && (
        <p role="status">Import a preset or create one in raw JSON before using guided creation.</p>
      )}

      <ul className="space-y-2" aria-label="Stored presets">
        {presets.map((preset) => (
          <li key={preset.name} className="flex items-center gap-2">
            <span>{preset.name}</span>
            <button
              type="button"
              disabled={!preset.body}
              onClick={() => preset.body && openDraft(preset.body, preset.name)}
            >
              Edit {preset.name}
            </button>
            <button type="button" onClick={() => void remove(preset.name)}>
              Delete {preset.name}
            </button>
          </li>
        ))}
      </ul>

      {draft && form && (
        <div className="space-y-3">
          <div className="flex gap-2" aria-label="Preset editor mode">
            <button
              type="button"
              aria-pressed={view === 'guided'}
              onClick={() => setView('guided')}
            >
              Guided fields
            </button>
            <button type="button" aria-pressed={view === 'raw'} onClick={() => setView('raw')}>
              Raw JSON
            </button>
          </div>

          {view === 'guided' ? (
            <div className="grid gap-2">
              <label>
                Name
                <input
                  value={form.name}
                  disabled={draft.originalName !== null}
                  onChange={(event) => updateGuided('name', event.target.value)}
                />
              </label>
              <label>
                Video encoder
                <input
                  value={form.encoder}
                  onChange={(event) => updateGuided('encoder', event.target.value)}
                />
              </label>
              <label>
                Speed preset
                <input
                  value={form.videoPreset}
                  onChange={(event) => updateGuided('videoPreset', event.target.value)}
                />
              </label>
              <label>
                File format
                <input
                  value={form.fileFormat}
                  onChange={(event) => updateGuided('fileFormat', event.target.value)}
                />
              </label>
              <label>
                Quality type
                <input
                  value={form.qualityType}
                  onChange={(event) => updateGuided('qualityType', event.target.value)}
                />
              </label>
              <label>
                Quality
                <input
                  value={form.quality}
                  onChange={(event) => updateGuided('quality', event.target.value)}
                />
              </label>
            </div>
          ) : (
            <label className="grid gap-1">
              Preset JSON
              <textarea
                value={draft.rawText}
                onChange={(event) => updateRaw(event.target.value)}
                rows={14}
              />
            </label>
          )}

          {draft.rawError && <p role="alert">{draft.rawError}</p>}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={Boolean(draft.rawError) || saving}
              onClick={() => void save()}
            >
              {saving ? 'Saving…' : 'Save preset'}
            </button>
            <button type="button" onClick={() => setDraft(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
