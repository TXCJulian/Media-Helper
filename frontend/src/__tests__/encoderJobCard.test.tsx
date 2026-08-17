import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import EncoderJobCard from '@/components/encoder/EncoderJobCard'
import type { EncoderJob } from '@/types'

const GiB = 1024 ** 3

function job(overrides: Partial<EncoderJob> = {}): EncoderJob {
  return {
    job_id: 'encode-1',
    source_path: '/media/Movies/Demo.mkv',
    stage: 'encoding',
    progress: 42,
    preset_name: 'NVENC',
    rule_id: 'uhd',
    error: null,
    error_code: null,
    output_path: null,
    facts: {},
    original_size: null,
    encoded_size: null,
    saved_bytes: null,
    created_at: '2026-08-17T08:00:00Z',
    updated_at: '2026-08-17T08:01:00Z',
    ...overrides,
  }
}

describe('EncoderJobCard', () => {
  it('renders completed savings and expandable media facts', async () => {
    render(
      <EncoderJobCard
        job={job({
          stage: 'done',
          progress: 100,
          original_size: 20 * GiB,
          encoded_size: 6 * GiB,
          saved_bytes: 14 * GiB,
          facts: { height: 2160, hdr: true },
        })}
        onApprove={vi.fn()}
        onDelete={vi.fn()}
      />,
    )

    expect(screen.getByText(/20\.0 GiB → 6\.0 GiB · 70% smaller/)).toBeTruthy()
    expect(screen.queryByText(/2160p/)).toBeNull()
    const details = screen.getByRole('button', { name: 'Show media details' })
    expect(details.querySelector('svg')).toBeTruthy()
    expect(details.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(details)
    expect(screen.getByText(/2160p/)).toBeTruthy()
    expect(screen.getByText(/HDR/)).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Hide media details' }).getAttribute('aria-expanded'),
    ).toBe('true')
  })

  it('shows the four-stage pipeline and a real encoding progress bar', () => {
    render(<EncoderJobCard job={job()} onApprove={vi.fn()} onDelete={vi.fn()} />)

    for (const label of ['Settling', 'Encoding', 'Swapping', 'Done']) {
      expect(screen.getByText(label)).toBeTruthy()
    }
    expect(screen.getByText('Encoding').className).not.toContain('rounded')
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('42')
  })

  it('renders a terminal outcome as a status pill without duplicating Done', () => {
    render(
      <EncoderJobCard
        job={job({ stage: 'done', progress: 100 })}
        onApprove={vi.fn()}
        onDelete={vi.fn()}
      />,
    )

    expect(screen.getAllByText('Done')).toHaveLength(1)
    expect(screen.getByText('Done').className).toContain('rounded')
  })

  it.each(['pending', 'blocked'] as const)(
    'offers approval, but not deletion, while %s',
    (stage) => {
      const onApprove = vi.fn()
      const onDelete = vi.fn()
      render(<EncoderJobCard job={job({ stage })} onApprove={onApprove} onDelete={onDelete} />)

      screen.getByRole('button', { name: 'Approve encoding' }).click()
      expect(onApprove).toHaveBeenCalledWith('encode-1')
      expect(screen.queryByRole('button', { name: 'Delete job' })).toBeNull()
    },
  )

  it('offers deletion for non-review jobs and surfaces backend errors', () => {
    const onDelete = vi.fn()
    render(
      <EncoderJobCard
        job={job({ stage: 'failed', error: 'HandBrake exited 1' })}
        onApprove={vi.fn()}
        onDelete={onDelete}
      />,
    )

    expect(screen.getByText('Failed').className).toContain('text-red')
    expect(screen.getByText('HandBrake exited 1')).toBeTruthy()
    screen.getByRole('button', { name: 'Delete job' }).click()
    expect(onDelete).toHaveBeenCalledWith('encode-1')
    expect(screen.queryByRole('button', { name: 'Approve encoding' })).toBeNull()
  })
})
