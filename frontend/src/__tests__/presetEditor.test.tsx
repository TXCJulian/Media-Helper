import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import PresetEditor from '@/components/encoder/PresetEditor'
import type { EncoderPreset } from '@/types'

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

describe('PresetEditor', () => {
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

  it('requires a seed before guided creation', () => {
    render(<PresetEditor presets={[]} onSaved={vi.fn()} onDeleted={vi.fn()} onError={vi.fn()} />)

    expect(screen.getByText(/Import a preset or create one in raw JSON/i)).toBeTruthy()
  })
})
