import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PresetEditor from '@/components/encoder/PresetEditor'
import type { EncoderPreset } from '@/types'

const api = vi.hoisted(() => ({
  saveEncoderPreset: vi.fn(),
  deleteEncoderPreset: vi.fn(),
  importEncoderPresets: vi.fn(),
}))

vi.mock('@/lib/api', () => api)

const leaf = {
  PresetName: 'NVENC',
  VideoEncoder: 'nvenc_h265',
  VideoPreset: 'medium',
  FileFormat: 'av_mkv',
  VideoQualityType: 2,
  VideoQualitySlider: 22,
}

function preset(overrides: Partial<EncoderPreset> = {}): EncoderPreset {
  return {
    name: 'NVENC',
    encoder: 'nvenc_h265',
    video_preset: 'medium',
    file_format: 'av_mkv',
    ...overrides,
  }
}

function renderEditor(presets: EncoderPreset[] = [preset({ body: leaf })]) {
  const onSaved = vi.fn()
  const onDeleted = vi.fn()
  const onError = vi.fn()
  const rendered = render(
    <PresetEditor presets={presets} onSaved={onSaved} onDeleted={onDeleted} onError={onError} />,
  )
  return { ...rendered, onSaved, onDeleted, onError }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('PresetEditor', () => {
  it('uses the encoder select surface in guided fields', () => {
    renderEditor()

    fireEvent.click(screen.getByRole('button', { name: 'New guided preset' }))

    expect(screen.getByLabelText('Video encoder').className).toContain('encoder-select')
  })

  it('preserves raw-only fields after guided changes', () => {
    render(
      <PresetEditor
        presets={[preset({ body: { ...leaf, AudioCopyMask: ['copy:aac'] } })]}
        onSaved={vi.fn()}
        onDeleted={vi.fn()}
        onError={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Edit NVENC' }))
    fireEvent.change(screen.getByLabelText('Speed preset'), { target: { value: 'slow' } })
    fireEvent.click(screen.getByRole('button', { name: 'Raw JSON' }))

    expect(
      JSON.parse((screen.getByLabelText('Preset JSON') as HTMLTextAreaElement).value),
    ).toMatchObject({
      VideoPreset: 'slow',
      AudioCopyMask: ['copy:aac'],
      VideoQualityType: 2,
      VideoQualitySlider: 22,
    })
  })

  it('requires a seed or encoder capability before guided creation', () => {
    render(<PresetEditor presets={[]} onSaved={vi.fn()} onDeleted={vi.fn()} onError={vi.fn()} />)

    expect(screen.getByText(/Import a preset or create one in raw JSON/i)).toBeTruthy()
  })

  it('keeps an existing preset name immutable when it is edited', async () => {
    api.saveEncoderPreset.mockResolvedValue({ body: leaf })
    const { onSaved } = renderEditor()

    fireEvent.click(screen.getByRole('button', { name: 'Edit NVENC' }))
    const name = screen.getByLabelText('Name') as HTMLInputElement
    expect(name.disabled).toBe(true)
    fireEvent.change(name, { target: { value: 'Duplicate NVENC' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save preset' }))

    await waitFor(() =>
      expect(api.saveEncoderPreset).toHaveBeenCalledWith('NVENC', expect.anything()),
    )
    expect(api.saveEncoderPreset.mock.calls[0]?.[1]).toMatchObject({ PresetName: 'NVENC' })
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('retains the last valid raw leaf and blocks save when raw JSON is invalid', () => {
    const { onSaved } = renderEditor()

    fireEvent.click(screen.getByRole('button', { name: 'Edit NVENC' }))
    fireEvent.click(screen.getByRole('button', { name: 'Raw JSON' }))
    fireEvent.change(screen.getByLabelText('Preset JSON'), { target: { value: '{ invalid' } })

    expect(screen.getByRole('alert').textContent).toMatch(/json/i)
    expect(
      (screen.getByRole('button', { name: 'Save preset' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Guided fields' }))
    expect((screen.getByLabelText('Speed preset') as HTMLInputElement).value).toBe('medium')
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('clones unknown nested fields for guided New without mutating its seed', () => {
    const body = { ...leaf, Extra: { nested: ['keep'] } }
    renderEditor([preset({ body })])

    fireEvent.click(screen.getByRole('button', { name: 'New guided preset' }))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Clone' } })
    fireEvent.click(screen.getByRole('button', { name: 'Raw JSON' }))

    expect(
      JSON.parse((screen.getByLabelText('Preset JSON') as HTMLTextAreaElement).value),
    ).toMatchObject({
      PresetName: 'Clone',
      Extra: { nested: ['keep'] },
    })
    expect(body).toEqual({ ...leaf, Extra: { nested: ['keep'] } })
  })

  it('opens raw New as an editable JSON object', () => {
    renderEditor()

    fireEvent.click(screen.getByRole('button', { name: 'New raw preset' }))
    const raw = screen.getByLabelText('Preset JSON') as HTMLTextAreaElement
    fireEvent.change(raw, { target: { value: '{\n  "PresetName": "Raw"\n}' } })

    expect(JSON.parse(raw.value)).toEqual({ PresetName: 'Raw' })
  })

  it('calls save success only after its upsert resolves', async () => {
    const pending = deferred<{ body: Record<string, unknown> }>()
    api.saveEncoderPreset.mockReturnValue(pending.promise)
    const { onSaved } = renderEditor()

    fireEvent.click(screen.getByRole('button', { name: 'Edit NVENC' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save preset' }))
    expect(onSaved).not.toHaveBeenCalled()

    pending.resolve({ body: leaf })
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
  })

  it('reports a rejected save without calling success', async () => {
    api.saveEncoderPreset.mockRejectedValue(new Error('save failed'))
    const { onSaved, onError } = renderEditor()

    fireEvent.click(screen.getByRole('button', { name: 'Edit NVENC' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save preset' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('save failed'))
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('calls delete success only after its request resolves and reports rejection without success', async () => {
    const pending = deferred<void>()
    api.deleteEncoderPreset
      .mockReturnValueOnce(pending.promise)
      .mockRejectedValueOnce(new Error('delete failed'))
    const first = renderEditor()

    fireEvent.click(screen.getByRole('button', { name: 'Delete NVENC' }))
    expect(first.onDeleted).not.toHaveBeenCalled()
    pending.resolve()
    await waitFor(() => expect(first.onDeleted).toHaveBeenCalledWith('NVENC'))
    first.unmount()

    const second = renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Delete NVENC' }))
    await waitFor(() => expect(second.onError).toHaveBeenCalledWith('delete failed'))
    expect(second.onDeleted).not.toHaveBeenCalled()
  })

  it('calls import success only after its request resolves and reports rejection without success', async () => {
    const pending = deferred<{ imported: string[]; skipped: [] }>()
    api.importEncoderPresets
      .mockReturnValueOnce(pending.promise)
      .mockRejectedValueOnce(new Error('import failed'))
    const file = new File(['{}'], 'presets.json', { type: 'application/json' })
    Object.defineProperty(file, 'text', { value: () => Promise.resolve('{}') })
    const first = renderEditor()

    fireEvent.change(first.container.querySelector('input[type="file"]')!, {
      target: { files: [file] },
    })
    await waitFor(() => expect(api.importEncoderPresets).toHaveBeenCalledTimes(1))
    expect(first.onSaved).not.toHaveBeenCalled()
    pending.resolve({ imported: [], skipped: [] })
    await waitFor(() => expect(first.onSaved).toHaveBeenCalledTimes(1))
    first.unmount()

    const second = renderEditor()
    fireEvent.change(second.container.querySelector('input[type="file"]')!, {
      target: { files: [file] },
    })
    await waitFor(() => expect(second.onError).toHaveBeenCalledWith('import failed'))
    expect(second.onSaved).not.toHaveBeenCalled()
  })
})
