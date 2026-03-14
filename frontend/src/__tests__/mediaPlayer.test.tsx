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

// jsdom does not implement media playback primitives used by the component.
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
    const { container } = render(<MediaPlayer {...baseProps()} />)

    expect(screen.getByText(/Transcoding preview/)).toBeTruthy()

    const video = container.querySelector('video')
    expect(video).toBeTruthy()
    fireEvent.error(video as HTMLVideoElement)

    expect(screen.queryByText(/Transcoding preview/)).toBeNull()
    expect(screen.getByText('Unknown media error')).toBeTruthy()
  })

  it('resets error on source change and marks ready on canPlay', () => {
    const { container, rerender } = render(<MediaPlayer {...baseProps()} />)

    const firstVideo = container.querySelector('video')
    fireEvent.error(firstVideo as HTMLVideoElement)
    expect(screen.getByText('Unknown media error')).toBeTruthy()

    rerender(<MediaPlayer {...baseProps({ streamUrl: '/cutter/stream/demo?audio_stream=2' })} />)
    expect(screen.queryByText('Unknown media error')).toBeNull()
    expect(screen.getByText(/Transcoding preview/)).toBeTruthy()

    const secondVideo = container.querySelector('video')
    fireEvent.canPlay(secondVideo as HTMLVideoElement)
    expect(screen.queryByText(/Transcoding preview/)).toBeNull()
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
