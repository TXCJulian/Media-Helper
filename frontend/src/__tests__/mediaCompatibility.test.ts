import { describe, expect, it } from 'vitest'

import {
  getBrowserCompatibilityMessage,
  getBrowserCompatibilityReport,
} from '@/lib/mediaCompatibility'
import type { ProbeResult } from '@/types'

function probe(overrides: Partial<ProbeResult> = {}): ProbeResult {
  return {
    duration: 100,
    video_codec: 'h264',
    audio_codec: 'aac',
    container: 'mov,mp4,m4a,3gp,3g2,mj2',
    bitrate: 0,
    width: 1920,
    height: 1080,
    sample_rate: 48000,
    needs_transcoding: false,
    audio_streams: [
      {
        index: 1,
        codec: 'aac',
        channels: 2,
        sample_rate: 48000,
        language: 'eng',
        title: '',
      },
    ],
    ...overrides,
  }
}

describe('mediaCompatibility', () => {
  it('returns no issues for browser-friendly media', () => {
    const report = getBrowserCompatibilityReport('Z:/Movies/clip.mp4', probe())
    expect(report.hasIssues).toBe(false)
    expect(report.containerIssue).toBeNull()
    expect(report.videoIssue).toBeNull()
    expect(report.audioIssues).toEqual([])
  })

  it('flags unsupported container extension', () => {
    const report = getBrowserCompatibilityReport('Z:/Movies/clip.mkv', probe())
    expect(report.hasIssues).toBe(true)
    expect(report.containerIssue).toBe('Video container .mkv')
  })

  it('flags unsupported audio codecs from tracks', () => {
    const report = getBrowserCompatibilityReport(
      'Z:/Movies/clip.mp4',
      probe({
        audio_streams: [
          { index: 1, codec: 'dts', channels: 6, sample_rate: 48000, language: 'eng', title: 'Main' },
          { index: 2, codec: 'aac', channels: 2, sample_rate: 48000, language: 'eng', title: 'Stereo' },
          { index: 3, codec: 'ac3', channels: 6, sample_rate: 48000, language: 'eng', title: 'Surround' },
        ],
      }),
    )

    expect(report.hasIssues).toBe(true)
    expect(report.audioIssues).toEqual(['DTS', 'AC3'])
  })

  it('formats a user-facing advisory message', () => {
    const report = getBrowserCompatibilityReport(
      'Z:/Movies/clip.mkv',
      probe({
        video_codec: 'hevc',
        audio_streams: [{ index: 1, codec: 'dts', channels: 6, sample_rate: 48000, language: '', title: '' }],
      }),
    )

    const message = getBrowserCompatibilityMessage(report)
    expect(message).toContain('Video container .mkv')
    expect(message).toContain('Video codec HEVC')
    expect(message).toContain('Audio track(s) DTS')
    expect(message).toContain('enable transcoding')
  })
})
