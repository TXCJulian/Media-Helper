import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useDirectoryAutoRefresh } from '@/hooks/useDirectoryAutoRefresh'

afterEach(() => {
  vi.useRealTimers()
})

describe('useDirectoryAutoRefresh', () => {
  it('calls the latest callback every 30 seconds without firing on mount', () => {
    vi.useFakeTimers()
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = renderHook(
      ({ refresh }) => useDirectoryAutoRefresh(refresh),
      { initialProps: { refresh: first } },
    )

    expect(first).not.toHaveBeenCalled()
    rerender({ refresh: second })
    vi.advanceTimersByTime(30_000)

    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledTimes(1)
  })

  it('clears the interval on unmount', () => {
    vi.useFakeTimers()
    const refresh = vi.fn()
    const { unmount } = renderHook(() => useDirectoryAutoRefresh(refresh))

    unmount()
    vi.advanceTimersByTime(60_000)

    expect(refresh).not.toHaveBeenCalled()
  })
})
