import { describe, it, expect } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'

import MediaPlayer from '@/components/cutter/MediaPlayer'

function baseProps(overrides: Partial<ComponentProps<typeof MediaPlayer>> = {}) {
  return {
    streamUrl: '/cutter/stream/demo',
    isVideo: true,
    peaks: [0.1, 0.2, 0.3],
    duration: 120,
    inPoint: 0,
    outPoint: 60,
    onInPointChange: () => undefined,
    onOutPointChange: () => undefined,
    thumbnailUrl: '/thumb.jpg',
    needsTranscoding: true,
    ...overrides,
  }
}

// jsdom does not implement ResizeObserver, canvas context, or media playback primitives.
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof globalThis.ResizeObserver

// Return a proxy that accepts any property access / method call.
const noop = () => undefined
const canvasCtxHandler: ProxyHandler<Record<string, unknown>> = {
  get(_target, prop) {
    if (prop === 'measureText') return () => ({ width: 0 })
    if (prop === 'createLinearGradient') return () => new Proxy({}, canvasCtxHandler)
    if (prop === 'canvas') return { width: 0, height: 0 }
    return noop
  },
  set() {
    return true
  },
}
HTMLCanvasElement.prototype.getContext = (() =>
  new Proxy({}, canvasCtxHandler)) as unknown as typeof HTMLCanvasElement.prototype.getContext

Object.defineProperty(HTMLMediaElement.prototype, 'pause', {
  configurable: true,
  value: () => undefined,
})
Object.defineProperty(HTMLMediaElement.prototype, 'load', {
  configurable: true,
  value: () => undefined,
})

describe('MediaPlayer', () => {
  it('exits transcoding state when media load fails', () => {
    const { container } = render(<MediaPlayer {...baseProps({ transcodeState: 'done' })} />)

    expect(screen.getByText(/Preparing stream/)).toBeTruthy()

    const video = container.querySelector('video')
    expect(video).toBeTruthy()
    fireEvent.error(video as HTMLVideoElement)

    expect(screen.queryByText(/Preparing stream/)).toBeNull()
    expect(screen.getByText(/Playback failed/)).toBeTruthy()
  })

  it('resets error on source change and marks ready on canPlay', () => {
    const { container, rerender } = render(<MediaPlayer {...baseProps()} />)

    const firstVideo = container.querySelector('video')
    fireEvent.error(firstVideo as HTMLVideoElement)
    expect(screen.getByText(/Playback failed/)).toBeTruthy()

    rerender(
      <MediaPlayer
        {...baseProps({ streamUrl: '/cutter/stream/demo?audio_stream=2', transcodeState: 'done' })}
      />,
    )
    expect(screen.queryByText('Unknown media error')).toBeNull()
    expect(screen.getByText(/Preparing stream/)).toBeTruthy()

    const secondVideo = container.querySelector('video')
    fireEvent.canPlay(secondVideo as HTMLVideoElement)
    expect(screen.queryByText(/Preparing stream/)).toBeNull()
  })

  it('shows percent and eta when provided', () => {
    render(
      <MediaPlayer
        {...baseProps({
          transcodePercent: 42.3,
          transcodeEtaSeconds: 17,
        })}
      />,
    )

    expect(screen.getByText('Transcoding preview')).toBeTruthy()
    expect(screen.getByText('42.3%')).toBeTruthy()
    expect(screen.getByText('ETA 17s')).toBeTruthy()
  })

  it('uses source video aspect ratio when dimensions are available', () => {
    const { container } = render(
      <MediaPlayer
        {...baseProps({
          videoWidth: 1920,
          videoHeight: 800,
        })}
      />,
    )

    const video = container.querySelector('video')
    expect(video).toBeTruthy()
    expect(video?.getAttribute('style')).toContain('aspect-ratio: 1920 / 800')
  })

  it('updates aspect ratio from loaded transcoded video metadata', () => {
    const { container } = render(
      <MediaPlayer
        {...baseProps({
          videoWidth: 720,
          videoHeight: 576,
        })}
      />,
    )

    const video = container.querySelector('video') as HTMLVideoElement | null
    expect(video).toBeTruthy()
    Object.defineProperty(video, 'videoWidth', { configurable: true, value: 1920 })
    Object.defineProperty(video, 'videoHeight', { configurable: true, value: 804 })
    fireEvent.loadedMetadata(video as HTMLVideoElement)

    expect(video?.getAttribute('style')).toContain('aspect-ratio: 1920 / 804')
  })

  it('prefers source display aspect ratio over raw dimensions before metadata loads', () => {
    const { container } = render(
      <MediaPlayer
        {...baseProps({
          sourceAspectRatio: '16 / 9',
          videoWidth: 720,
          videoHeight: 576,
        })}
      />,
    )

    const video = container.querySelector('video')
    expect(video).toBeTruthy()
    expect(video?.getAttribute('style')).toContain('aspect-ratio: 16 / 9')
  })
})

describe('trim bounds with an unset out point', () => {
  // A job saved without cut_settings reopens with outPoint = 0. enforceTrimBounds
  // compared `currentTime >= outPoint - tolerance`, which is always true at 0, so
  // every timeupdate seeked back to the in point — and each seek on a
  // preload="none" element with nothing buffered issues a fresh range request.
  // That was the ~10 requests/second storm after a transcode finished.
  it('does not seek when the out point is zero', () => {
    const { container } = render(<MediaPlayer {...baseProps({ outPoint: 0, inPoint: 0 })} />)
    const video = container.querySelector('video') as HTMLVideoElement

    let seeks = 0
    let time = 5
    Object.defineProperty(video, 'currentTime', {
      configurable: true,
      get: () => time,
      set: (v: number) => {
        seeks += 1
        time = v
      },
    })

    fireEvent.timeUpdate(video)
    fireEvent.timeUpdate(video)
    fireEvent.timeUpdate(video)

    expect(seeks).toBe(0)
  })

  it('still loops back to the in point once a real out point is reached', () => {
    const { container } = render(<MediaPlayer {...baseProps({ inPoint: 2, outPoint: 10 })} />)
    const video = container.querySelector('video') as HTMLVideoElement

    const written: number[] = []
    let time = 10
    Object.defineProperty(video, 'currentTime', {
      configurable: true,
      get: () => time,
      set: (v: number) => {
        written.push(v)
        time = v
      },
    })

    fireEvent.timeUpdate(video)

    expect(written).toContain(2)
  })
})
