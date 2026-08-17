import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import EncoderSettings from '@/components/encoder/EncoderSettings'
import * as api from '@/lib/api'
import type { EncoderConfig, EncoderPreset, EncoderRule } from '@/types'

vi.mock('@/lib/api', () => ({
  saveEncoderConfig: vi.fn(),
  saveEncoderRules: vi.fn(),
  testEncoderFile: vi.fn(),
  reprocessEncoderFile: vi.fn(),
  saveEncoderPreset: vi.fn(),
  deleteEncoderPreset: vi.fn(),
  importEncoderPresets: vi.fn(),
}))

const config: EncoderConfig = {
  watch_paths: ['/media/Movies'],
  mode: 'review',
  settle_seconds: 30,
  original_ttl: 86_400,
  job_ttl: 604_800,
}

const presets: EncoderPreset[] = [
  {
    name: 'NVENC',
    encoder: 'nvenc_h265',
    video_preset: 'slow',
    file_format: 'av_mkv',
    body: {
      PresetName: 'NVENC',
      VideoEncoder: 'nvenc_h265',
      VideoPreset: 'slow',
      FileFormat: 'av_mkv',
    },
  },
]

const orderedRules: EncoderRule[] = [
  {
    id: 'uhd',
    conditions: [{ field: 'height', op: '>=', value: 2160 }],
    target: 'NVENC',
  },
  {
    id: 'already-hevc',
    conditions: [{ field: 'video_codec', op: '==', value: 'hevc' }],
    target: 'skip',
  },
]

function renderSettings(overrides: Partial<React.ComponentProps<typeof EncoderSettings>> = {}) {
  const onRefresh = vi.fn()
  const onError = vi.fn()
  render(
    <EncoderSettings
      config={config}
      presets={presets}
      rules={{ rules: orderedRules, fallback: 'skip' }}
      onRefresh={onRefresh}
      onError={onError}
      {...overrides}
    />,
  )
  return { onRefresh, onError }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.saveEncoderConfig).mockResolvedValue({ watch_paths: [] })
  vi.mocked(api.saveEncoderRules).mockResolvedValue({ saved: 0 })
  vi.mocked(api.reprocessEncoderFile).mockResolvedValue({
    path: '/media/Movies/demo.mkv',
    cleared: true,
  })
})

describe('EncoderSettings sections', () => {
  it('starts collapsed and keeps the fixed fallback after every ordered rule', async () => {
    renderSettings()

    expect(screen.queryByText('Fallback')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))

    const fallback = screen.getByText('Fallback').parentElement
    expect(fallback?.className).toContain('border-dashed')
    const rows = screen.getByRole('list', { name: 'Encoding rules' }).children
    expect(rows[rows.length - 1]).toBe(fallback)
    expect(screen.queryByRole('button', { name: /Move fallback/i })).toBeNull()
  })

  it('adds and removes watch folders locally, then saves the explicit list', async () => {
    const { onRefresh } = renderSettings()

    fireEvent.click(screen.getByRole('button', { name: 'Watch Folders' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add watch folder' }))
    fireEvent.change(screen.getByLabelText('Watch folder 2'), {
      target: { value: '/media/Shows' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Remove watch folder 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save watch folders' }))

    await waitFor(() => {
      expect(api.saveEncoderConfig).toHaveBeenCalledWith(['/media/Shows'])
      expect(onRefresh).toHaveBeenCalledTimes(1)
    })
  })

  it('moves rules, adds AND conditions, and saves their visible order', async () => {
    const { onRefresh } = renderSettings()

    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add condition to uhd' }))
    expect(screen.getByText('AND')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Move rule already-hevc up' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save rules' }))

    await waitFor(() => {
      const payload = vi.mocked(api.saveEncoderRules).mock.calls[0]![0]
      expect(payload.rules.map((rule) => rule.id)).toEqual(['already-hevc', 'uhd'])
      expect(payload.rules[1]!.conditions).toHaveLength(2)
      expect(payload.fallback).toBe('skip')
      expect(onRefresh).toHaveBeenCalledTimes(1)
    })
  })

  it('keeps rule-name focus while editing and offers only valid numeric operators', () => {
    renderSettings()
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))

    const name = screen.getByLabelText('Rule 1 name')
    name.focus()
    fireEvent.change(name, { target: { value: 'uhd-4k' } })
    expect(document.activeElement).toBe(name)

    const operator = screen.getByLabelText('uhd-4k condition 1 operator') as HTMLSelectElement
    expect(Array.from(operator.options, (option) => option.value)).not.toContain('contains')
  })

  it('shows matched, evaluated, and unreachable rules when testing a file', async () => {
    vi.mocked(api.testEncoderFile).mockResolvedValue({
      facts: { height: 2160, hdr: true },
      matched_rule: 'uhd',
      target: 'NVENC',
      evaluated: ['uhd'],
      not_evaluated: ['already-hevc'],
    })
    renderSettings()

    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    fireEvent.change(screen.getByLabelText('File to test'), {
      target: { value: '/media/Movies/demo.mkv' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Test file' }))

    expect(await screen.findByText('Matched: uhd → NVENC')).toBeTruthy()
    expect(screen.getByText('Evaluated: uhd')).toBeTruthy()
    expect(screen.getByText('Not evaluated: already-hevc')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Reprocess file' }))
    await waitFor(() =>
      expect(api.reprocessEncoderFile).toHaveBeenCalledWith('/media/Movies/demo.mkv'),
    )
    expect(await screen.findByText(/queued for reconsideration/i)).toBeTruthy()
  })

  it('forwards failed saves without reporting a refresh', async () => {
    vi.mocked(api.saveEncoderConfig).mockRejectedValue(new Error('outside allowed roots'))
    const { onRefresh, onError } = renderSettings()

    fireEvent.click(screen.getByRole('button', { name: 'Watch Folders' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save watch folders' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('outside allowed roots'))
    expect(onRefresh).not.toHaveBeenCalled()
  })
})
