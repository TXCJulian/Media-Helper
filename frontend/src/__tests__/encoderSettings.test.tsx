import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import EncoderSettings from '@/components/encoder/EncoderSettings'
import DirectorySelect from '@/components/ui/DirectorySelect'
import * as api from '@/lib/api'
import type {
  EncoderConfig,
  EncoderPreset,
  EncoderReprocessEvent,
  EncoderReprocessRun,
  EncoderRule,
  EncoderTestResult,
  ReprocessResult,
} from '@/types'

vi.mock('@/lib/api', () => ({
  fetchEncoderDirectories: vi.fn().mockResolvedValue({ directories: [] }),
  fetchEncoderFiles: vi.fn().mockResolvedValue({ files: [] }),
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

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function testResult(overrides: Partial<EncoderTestResult> = {}): EncoderTestResult {
  return {
    facts: {},
    matched_rule: 'uhd',
    target: 'NVENC',
    evaluated: ['uhd'],
    not_evaluated: ['already-hevc'],
    ...overrides,
  }
}

function renderSettings(overrides: Partial<React.ComponentProps<typeof EncoderSettings>> = {}) {
  const onRefresh = vi.fn()
  const onError = vi.fn()
  const rendered = render(
    <EncoderSettings
      config={config}
      presets={presets}
      rules={{ rules: orderedRules, fallback: 'skip' }}
      onRefresh={onRefresh}
      onError={onError}
      {...overrides}
    />,
  )
  return { onRefresh, onError, ...rendered }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.saveEncoderConfig).mockResolvedValue({ watch_paths: [] })
  vi.mocked(api.saveEncoderRules).mockResolvedValue({ saved: 0 })
  vi.mocked(api.reprocessEncoderFile).mockResolvedValue({
    job_id: 'new-job',
    path: '/media/Movies/demo.mkv',
    stage: 'pending',
    created: true,
  })
})

describe('EncoderSettings sections', () => {
  it('focuses the first directory result when an open picker lacks the current value', () => {
    const { rerender } = render(
      <DirectorySelect
        directories={[]}
        value="/media/missing"
        base=""
        onChange={vi.fn()}
        onRefresh={vi.fn()}
        isLoading={false}
        ariaLabel="Directory picker"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Directory picker' }))
    rerender(
      <DirectorySelect
        directories={[{ path: '/media/Movies', base: '/media' }]}
        value="/media/missing"
        base=""
        onChange={vi.fn()}
        onRefresh={vi.fn()}
        isLoading={false}
        ariaLabel="Directory picker"
      />,
    )

    expect(screen.getByRole('option', { name: '/media/Movies' }).className).toContain(
      'bg-[var(--bg-glass-hover)]',
    )
  })

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

  it('requires confirmation before re-evaluating all media and reports run progress', async () => {
    const onStartReprocessAll = vi.fn().mockResolvedValue({ run_id: 'bulk-1', status: 'started' })
    const latestReprocessEvent: EncoderReprocessEvent = {
      type: 'reprocess',
      run_id: 'bulk-1',
      status: 'running',
      scanned: 12,
      created: 4,
      skipped: 7,
      failed: 1,
      path: '/media/Movies/Demo.mkv',
      error: null,
    }
    const { rerender } = renderSettings({ onStartReprocessAll })

    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    const reprocess = screen.getByRole('button', { name: 'Re-evaluate all media' })
    fireEvent.click(reprocess)
    expect(onStartReprocessAll).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm re-evaluate all media' }))
    await waitFor(() => expect(onStartReprocessAll).toHaveBeenCalledTimes(1))
    rerender(
      <EncoderSettings
        config={config}
        presets={presets}
        rules={{ rules: orderedRules, fallback: 'skip' }}
        onRefresh={vi.fn()}
        onError={vi.fn()}
        onStartReprocessAll={onStartReprocessAll}
        latestReprocessEvent={latestReprocessEvent}
      />,
    )
    expect(screen.getByText(/12 scanned.*4 awaiting review.*7 skipped.*1 failed/i)).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Re-evaluate all media' }).hasAttribute('disabled'),
    ).toBe(true)
  })

  it('labels created bulk results according to review mode', () => {
    renderSettings({
      latestReprocessEvent: {
        type: 'reprocess',
        run_id: 'bulk-review',
        status: 'completed',
        scanned: 3,
        created: 2,
        skipped: 1,
        failed: 0,
        path: null,
        error: null,
      },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    expect(screen.getByText(/2 awaiting review/i)).toBeTruthy()
  })

  it('keeps a started bulk run single-flight until its matching terminal event arrives', async () => {
    const pending = deferred<EncoderReprocessRun>()
    const onStartReprocessAll = vi.fn().mockReturnValue(pending.promise)
    const { rerender } = renderSettings({ onStartReprocessAll })
    const renderWithEvent = (latestReprocessEvent: EncoderReprocessEvent | null) =>
      rerender(
        <EncoderSettings
          config={config}
          presets={presets}
          rules={{ rules: orderedRules, fallback: 'skip' }}
          onRefresh={vi.fn()}
          onError={vi.fn()}
          onStartReprocessAll={onStartReprocessAll}
          latestReprocessEvent={latestReprocessEvent}
        />,
      )

    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    fireEvent.click(screen.getByRole('button', { name: 'Re-evaluate all media' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm re-evaluate all media' }))
    await waitFor(() => expect(onStartReprocessAll).toHaveBeenCalledTimes(1))

    await act(async () => pending.resolve({ run_id: 'bulk-1', status: 'started' }))
    expect(
      screen.getByRole('button', { name: 'Re-evaluate all media' }).hasAttribute('disabled'),
    ).toBe(true)

    renderWithEvent({
      type: 'reprocess',
      run_id: 'bulk-1',
      status: 'completed',
      scanned: 12,
      created: 4,
      skipped: 7,
      failed: 1,
      path: null,
      error: null,
    })
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Re-evaluate all media' }).hasAttribute('disabled'),
      ).toBe(false),
    )
  })

  it('releases a local run lock when recovered status says no run is active', async () => {
    const onStartReprocessAll = vi.fn().mockResolvedValue({ run_id: 'bulk-1', status: 'started' })
    const { rerender } = renderSettings({ onStartReprocessAll, reprocessActive: true })
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    fireEvent.click(screen.getByRole('button', { name: 'Re-evaluate all media' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm re-evaluate all media' }))
    await waitFor(() => expect(onStartReprocessAll).toHaveBeenCalledTimes(1))

    rerender(
      <EncoderSettings
        config={config}
        presets={presets}
        rules={{ rules: orderedRules, fallback: 'skip' }}
        onRefresh={vi.fn()}
        onError={vi.fn()}
        onStartReprocessAll={onStartReprocessAll}
        reprocessActive={false}
      />,
    )

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Re-evaluate all media' }).hasAttribute('disabled'),
      ).toBe(false),
    )
  })

  it('does not retain a run lock when its terminal event arrives before the start response', async () => {
    const pending = deferred<EncoderReprocessRun>()
    const onStartReprocessAll = vi.fn().mockReturnValue(pending.promise)
    const { rerender } = renderSettings({ onStartReprocessAll })

    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    fireEvent.click(screen.getByRole('button', { name: 'Re-evaluate all media' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm re-evaluate all media' }))
    await waitFor(() => expect(onStartReprocessAll).toHaveBeenCalledTimes(1))

    rerender(
      <EncoderSettings
        config={config}
        presets={presets}
        rules={{ rules: orderedRules, fallback: 'skip' }}
        onRefresh={vi.fn()}
        onError={vi.fn()}
        onStartReprocessAll={onStartReprocessAll}
        latestReprocessEvent={{
          type: 'reprocess',
          run_id: 'bulk-2',
          status: 'completed',
          scanned: 0,
          created: 0,
          skipped: 0,
          failed: 0,
          path: null,
          error: null,
        }}
      />,
    )
    await act(async () => pending.resolve({ run_id: 'bulk-2', status: 'started' }))

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Re-evaluate all media' }).hasAttribute('disabled'),
      ).toBe(false),
    )
  })

  it('uses compact encoder select labels for rules and fallback targets', () => {
    renderSettings()

    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))

    expect(screen.getAllByText('Field')[0]?.closest('label')?.className).toContain('text-[0.7rem]')
    expect(screen.getAllByText('Operator')[0]?.closest('label')?.className).toContain(
      'text-[0.7rem]',
    )
    expect(screen.getAllByText('Value')[0]?.closest('label')?.className).toContain('text-[0.7rem]')
    expect(screen.getAllByText('Target')[0]?.closest('label')?.className).toContain('text-[0.7rem]')
    expect(screen.getByText('Fallback target').closest('label')?.className).toContain(
      'text-[0.7rem]',
    )
  })

  it('provides makemkv and standard tools for source_tool conditions', () => {
    renderSettings({
      rules: {
        rules: [
          {
            id: 'rule-1',
            conditions: [{ field: 'source_tool', op: '==', value: 'makemkv' }],
            target: 'CPU 4K',
          },
        ],
        fallback: 'skip',
      },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))

    const select = screen.getByLabelText('rule-1 condition 1 value') as HTMLSelectElement
    const options = Array.from(select.options).map((opt) => opt.value)
    expect(options).toContain('makemkv')
    expect(options).toContain('handbrake')
    expect(options).toContain('lavf')
  })

  it('selects the first returned directory and file for a blank file test', async () => {
    vi.mocked(api.fetchEncoderDirectories).mockResolvedValue({
      directories: [{ path: '/media/Movies', base: '/media' }],
    })
    vi.mocked(api.fetchEncoderFiles).mockResolvedValue({
      files: [{ path: '/media/Movies/demo.mkv', name: 'demo.mkv' }],
    })
    renderSettings()

    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))

    const selector = (await screen.findByLabelText('File to test selector')) as HTMLSelectElement
    await waitFor(() => expect(selector.value).toBe('/media/Movies/demo.mkv'))
    expect(api.fetchEncoderFiles).toHaveBeenCalledWith('/media/Movies', '')
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

  it('creates a unique default rule ID after another rule is removed', () => {
    renderSettings({
      rules: {
        rules: [
          { id: 'rule-1', conditions: [], target: 'skip' },
          { id: 'rule-2', conditions: [], target: 'skip' },
        ],
        fallback: 'skip',
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))

    fireEvent.click(screen.getByRole('button', { name: 'Remove rule rule-1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))

    const ids = screen
      .getAllByLabelText(/Rule \d+ name/)
      .map((input) => (input as HTMLInputElement).value)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('rejects an empty edited rule ID before saving', () => {
    const { onError } = renderSettings()
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    fireEvent.change(screen.getByLabelText('Rule 1 name'), { target: { value: '   ' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save rules' }))

    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/empty/i))
    expect(api.saveEncoderRules).not.toHaveBeenCalled()
  })

  it('rejects duplicate edited rule IDs before saving', () => {
    const { onError } = renderSettings()
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    fireEvent.change(screen.getByLabelText('Rule 2 name'), { target: { value: 'uhd' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save rules' }))

    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/unique/i))
    expect(api.saveEncoderRules).not.toHaveBeenCalled()
  })

  it('shows matched, evaluated, and unreachable rules when testing a file', async () => {
    vi.mocked(api.testEncoderFile).mockResolvedValue(
      testResult({ facts: { height: 2160, hdr: true } }),
    )
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
    expect(await screen.findByText(/re-evaluated immediately/i)).toBeTruthy()
  })

  it('clears completed file-test and reprocess output when the path changes', async () => {
    vi.mocked(api.testEncoderFile).mockResolvedValue(testResult())
    renderSettings()
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    const input = screen.getByLabelText('File to test')

    fireEvent.change(input, { target: { value: '/media/Movies/a.mkv' } })
    fireEvent.click(screen.getByRole('button', { name: 'Test file' }))
    expect(await screen.findByText('Matched: uhd → NVENC')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Reprocess file' }))
    expect(await screen.findByText(/re-evaluated immediately/i)).toBeTruthy()

    fireEvent.change(input, { target: { value: '/media/Movies/b.mkv' } })
    expect(screen.queryByText('Matched: uhd → NVENC')).toBeNull()
    expect(screen.queryByText(/queued for reconsideration/i)).toBeNull()
  })

  it('ignores a file-test response after its submitted path is replaced', async () => {
    const pending = deferred<EncoderTestResult>()
    vi.mocked(api.testEncoderFile).mockReturnValue(pending.promise)
    renderSettings()
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    const input = screen.getByLabelText('File to test')

    fireEvent.change(input, { target: { value: '/media/Movies/a.mkv' } })
    fireEvent.click(screen.getByRole('button', { name: 'Test file' }))
    fireEvent.change(input, { target: { value: '/media/Movies/b.mkv' } })
    await act(async () => pending.resolve(testResult()))

    expect(screen.queryByText('Matched: uhd → NVENC')).toBeNull()
  })

  it('ignores a reprocess response after its submitted path is replaced', async () => {
    const pending = deferred<ReprocessResult>()
    vi.mocked(api.reprocessEncoderFile).mockReturnValue(pending.promise)
    renderSettings()
    fireEvent.click(screen.getByRole('button', { name: 'Rules' }))
    const input = screen.getByLabelText('File to test')

    fireEvent.change(input, { target: { value: '/media/Movies/a.mkv' } })
    fireEvent.click(screen.getByRole('button', { name: 'Reprocess file' }))
    fireEvent.change(input, { target: { value: '/media/Movies/b.mkv' } })
    await act(async () =>
      pending.resolve({
        job_id: 'new-job',
        path: '/media/Movies/a.mkv',
        stage: 'pending',
        created: true,
      }),
    )

    expect(screen.queryByText(/re-evaluated immediately/i)).toBeNull()
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
