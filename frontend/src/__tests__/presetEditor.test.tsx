import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PresetEditor from '@/components/encoder/PresetEditor'
import type { EncoderPreset } from '@/types'

const api = vi.hoisted(() => ({
  saveEncoderPreset: vi.fn(),
  deleteEncoderPreset: vi.fn(),
  previewEncoderPresets: vi.fn(),
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

const STOCK_DOCUMENT = {
  PresetList: [leaf],
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
    const pending = deferred<{ imported: string[]; skipped: []; unselected: [] }>()
    api.previewEncoderPresets.mockResolvedValue({
      presets: [{ name: 'NVENC', encoder: 'nvenc_h265', supported: true, reason: null }],
    })
    api.importEncoderPresets
      .mockReturnValueOnce(pending.promise)
      .mockRejectedValueOnce(new Error('import failed'))
    const file = new File(['{}'], 'presets.json', { type: 'application/json' })
    Object.defineProperty(file, 'text', { value: () => Promise.resolve('{}') })
    const first = renderEditor()

    fireEvent.change(first.container.querySelector('input[type="file"]')!, {
      target: { files: [file] },
    })
    await screen.findByLabelText('Keep preset NVENC')
    fireEvent.click(screen.getByRole('button', { name: 'Import selected presets' }))
    await waitFor(() => expect(api.importEncoderPresets).toHaveBeenCalledTimes(1))
    expect(first.onSaved).not.toHaveBeenCalled()
    pending.resolve({ imported: [], skipped: [], unselected: [] })
    await waitFor(() => expect(first.onSaved).toHaveBeenCalledTimes(1))
    first.unmount()

    const second = renderEditor()
    fireEvent.change(second.container.querySelector('input[type="file"]')!, {
      target: { files: [file] },
    })
    await screen.findByLabelText('Keep preset NVENC')
    fireEvent.click(screen.getByRole('button', { name: 'Import selected presets' }))
    await waitFor(() => expect(second.onError).toHaveBeenCalledWith('import failed'))
    expect(second.onSaved).not.toHaveBeenCalled()
  })

  it('lets the user deselect a supported preset before import', async () => {
    api.previewEncoderPresets.mockResolvedValue({
      presets: [{ name: 'NVENC', encoder: 'nvenc_h265', supported: true, reason: null }],
    })
    api.importEncoderPresets.mockResolvedValue({ imported: [], skipped: [], unselected: ['NVENC'] })
    const file = new File([JSON.stringify(STOCK_DOCUMENT)], 'presets.json', {
      type: 'application/json',
    })
    Object.defineProperty(file, 'text', {
      value: () => Promise.resolve(JSON.stringify(STOCK_DOCUMENT)),
    })
    const { container } = renderEditor()

    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await screen.findByLabelText('Keep preset NVENC')
    fireEvent.click(screen.getByLabelText('Keep preset NVENC'))
    fireEvent.click(screen.getByRole('button', { name: 'Import selected presets' }))

    await waitFor(() => expect(api.importEncoderPresets).toHaveBeenCalledWith(STOCK_DOCUMENT, []))
  })

  it('keeps the raw editor viewport bounded for long JSON', () => {
    renderEditor()

    fireEvent.click(screen.getByRole('button', { name: 'New raw preset' }))
    const editor = screen.getByLabelText('Preset JSON')

    expect(editor.className).toContain('h-full')
    expect(editor.className).toContain('overflow-auto')
    expect(editor.className).not.toContain('resize-y')
  })

  it('keeps long raw overlay content available while syncing both scroll axes', () => {
    const { container } = renderEditor()
    const rawText = JSON.stringify(
      {
        ...leaf,
        LongValues: Array.from(
          { length: 40 },
          (_, index) => `line-${index + 1}-${'x'.repeat(120)}`,
        ),
      },
      null,
      2,
    )

    fireEvent.click(screen.getByRole('button', { name: 'New raw preset' }))
    const editor = screen.getByLabelText('Preset JSON') as HTMLTextAreaElement
    fireEvent.change(editor, { target: { value: rawText } })
    fireEvent.scroll(editor, { target: { scrollTop: 140, scrollLeft: 90 } })

    const overlays = container.querySelectorAll('pre')
    expect(overlays).toHaveLength(2)
    const lineNumbers = overlays[0]!
    const highlight = overlays[1]!
    expect(lineNumbers.textContent).toContain(String(rawText.split('\n').length))
    expect(highlight.textContent).toContain(`line-40-${'x'.repeat(120)}`)
    expect(lineNumbers.style.transform).toBe('translateY(-140px)')
    expect(highlight.style.transform).toBe('translate(-90px, -140px)')
    expect(lineNumbers.className).not.toContain('overflow-hidden')
    expect(highlight.className).not.toContain('overflow-hidden')
    expect(editor.parentElement?.parentElement?.className).toContain('overflow-hidden')
  })

  it('preserves raw typing text while keeping an existing preset name immutable on save', async () => {
    api.saveEncoderPreset.mockResolvedValue({ body: leaf })
    renderEditor()
    const rawText = JSON.stringify({ ...leaf, PresetName: 'Renamed NVENC', Extra: true }, null, 4)

    fireEvent.click(screen.getByRole('button', { name: 'Edit NVENC' }))
    fireEvent.click(screen.getByRole('button', { name: 'Raw JSON' }))
    fireEvent.change(screen.getByLabelText('Preset JSON'), { target: { value: rawText } })

    expect((screen.getByLabelText('Preset JSON') as HTMLTextAreaElement).value).toBe(rawText)
    fireEvent.click(screen.getByRole('button', { name: 'Save preset' }))
    await waitFor(() => expect(api.saveEncoderPreset).toHaveBeenCalled())
    expect(api.saveEncoderPreset).toHaveBeenCalledWith(
      'NVENC',
      expect.objectContaining({ PresetName: 'NVENC', Extra: true }),
    )
  })

  it('shows unsupported preview candidates as disabled with their reason', async () => {
    api.previewEncoderPresets.mockResolvedValue({
      presets: [
        {
          name: 'QSV',
          encoder: 'qsv_h265',
          supported: false,
          reason: "The connected encoder does not provide 'qsv_h265'",
        },
      ],
    })
    const file = new File([JSON.stringify(STOCK_DOCUMENT)], 'presets.json', {
      type: 'application/json',
    })
    Object.defineProperty(file, 'text', {
      value: () => Promise.resolve(JSON.stringify(STOCK_DOCUMENT)),
    })
    const { container } = renderEditor()

    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } })

    const candidate = await screen.findByLabelText('Keep preset QSV')
    expect(candidate).toHaveProperty('disabled', true)
    expect(screen.getByText(/does not provide 'qsv_h265'/)).toBeTruthy()
  })

  it('reports imported, unselected, and unsupported presets in the import status', async () => {
    api.previewEncoderPresets.mockResolvedValue({
      presets: [
        { name: 'NVENC', encoder: 'nvenc_h265', supported: true, reason: null },
        {
          name: 'QSV',
          encoder: 'qsv_h265',
          supported: false,
          reason: "The connected encoder does not provide 'qsv_h265'",
        },
      ],
    })
    api.importEncoderPresets.mockResolvedValue({
      imported: ['NVENC'],
      skipped: [],
      unselected: ['QSV'],
    })
    const file = new File([JSON.stringify(STOCK_DOCUMENT)], 'presets.json', {
      type: 'application/json',
    })
    Object.defineProperty(file, 'text', {
      value: () => Promise.resolve(JSON.stringify(STOCK_DOCUMENT)),
    })
    const { container } = renderEditor()

    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await screen.findByLabelText('Keep preset NVENC')
    fireEvent.click(screen.getByRole('button', { name: 'Import selected presets' }))

    const status = await screen.findByRole('status')
    expect(status.textContent).toContain('Imported 1 preset.')
    expect(status.textContent).toContain('Not imported: QSV.')
    expect(status.textContent).toContain("QSV: The connected encoder does not provide 'qsv_h265'")
  })
})
