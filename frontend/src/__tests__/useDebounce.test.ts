import { describe, it, expect, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useDebounce } from '@/hooks/useDebounce'

describe('useDebounce', () => {
  it('returns initial value immediately', () => {
    const { result } = renderHook(() => useDebounce('hello', 500))
    expect(result.current).toBe('hello')
  })

  it('debounces value changes', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(({ value, delay }) => useDebounce(value, delay), {
      initialProps: { value: 'initial', delay: 500 },
    })

    expect(result.current).toBe('initial')

    rerender({ value: 'updated', delay: 500 })
    expect(result.current).toBe('initial')

    act(() => {
      vi.advanceTimersByTime(500)
    })
    expect(result.current).toBe('updated')

    vi.useRealTimers()
  })

  it('resets timer on rapid changes', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(({ value, delay }) => useDebounce(value, delay), {
      initialProps: { value: 'a', delay: 300 },
    })

    rerender({ value: 'b', delay: 300 })
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(result.current).toBe('a')

    rerender({ value: 'c', delay: 300 })
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(result.current).toBe('a')

    act(() => {
      vi.advanceTimersByTime(300)
    })
    expect(result.current).toBe('c')

    vi.useRealTimers()
  })
})
