import { describe, it, expect } from 'vitest'
import {
  isVideoCodecCompatible,
  isAudioCodecCompatible,
  isPassthruCompatible,
  incompatibleVideoCodecs,
  incompatibleContainers,
  audioCodecsForContainer,
  bestContainerForCodec,
  bestCodecForContainer,
  bestAudioCodecForContainer,
} from '@/lib/codecCompat'

const videoCodecOptions = [
  { label: 'H.264', value: 'libx264' },
  { label: 'H.265', value: 'libx265' },
  { label: 'VP9', value: 'libvpx-vp9' },
  { label: 'AV1', value: 'libsvtav1' },
]

const containerOptions = [
  { label: 'MP4', value: 'mp4' },
  { label: 'MKV', value: 'mkv' },
  { label: 'WebM', value: 'webm' },
  { label: 'MOV', value: 'mov' },
]

const audioCodecOpts = [
  { label: 'AAC', value: 'aac' },
  { label: 'AC3', value: 'ac3' },
  { label: 'EAC3', value: 'eac3' },
  { label: 'DTS', value: 'dts' },
  { label: 'TrueHD', value: 'truehd' },
  { label: 'FLAC', value: 'flac' },
  { label: 'Opus', value: 'opus' },
  { label: 'Vorbis', value: 'vorbis' },
  { label: 'MP3', value: 'mp3' },
]

describe('isVideoCodecCompatible', () => {
  it('allows H.264 in MP4', () => {
    expect(isVideoCodecCompatible('libx264', 'mp4')).toBe(true)
  })

  it('rejects VP9 in MP4', () => {
    expect(isVideoCodecCompatible('libvpx-vp9', 'mp4')).toBe(false)
  })

  it('allows all codecs in MKV', () => {
    for (const opt of videoCodecOptions) {
      expect(isVideoCodecCompatible(opt.value, 'mkv')).toBe(true)
    }
  })

  it('allows any codec in unknown container', () => {
    expect(isVideoCodecCompatible('libx264', 'avi')).toBe(true)
  })
})

describe('isAudioCodecCompatible', () => {
  it('allows AAC in MP4', () => {
    expect(isAudioCodecCompatible('aac', 'mp4')).toBe(true)
  })

  it('rejects FLAC in MP4', () => {
    expect(isAudioCodecCompatible('flac', 'mp4')).toBe(false)
  })

  it('allows DTS in MKV', () => {
    expect(isAudioCodecCompatible('dts', 'mkv')).toBe(true)
  })

  it('allows TrueHD in MKV', () => {
    expect(isAudioCodecCompatible('truehd', 'mkv')).toBe(true)
  })

  it('rejects DTS in MP4', () => {
    expect(isAudioCodecCompatible('dts', 'mp4')).toBe(false)
  })
})

describe('isPassthruCompatible', () => {
  it('allows passthru for any codec in MKV', () => {
    expect(isPassthruCompatible('truehd', 'mkv')).toBe(true)
    expect(isPassthruCompatible('dts', 'mkv')).toBe(true)
    expect(isPassthruCompatible('aac', 'mkv')).toBe(true)
  })

  it('blocks DTS passthru in WebM', () => {
    expect(isPassthruCompatible('dts', 'webm')).toBe(false)
  })

  it('blocks TrueHD passthru in OGG', () => {
    expect(isPassthruCompatible('truehd', 'ogg')).toBe(false)
  })

  it('allows passthru in MP4 (not blacklisted)', () => {
    expect(isPassthruCompatible('aac', 'mp4')).toBe(true)
  })

  it('is case-insensitive', () => {
    expect(isPassthruCompatible('TRUEHD', 'mkv')).toBe(true)
    expect(isPassthruCompatible('DTS', 'webm')).toBe(false)
  })
})

describe('incompatibleVideoCodecs', () => {
  it('returns VP9 as incompatible with MP4', () => {
    const result = incompatibleVideoCodecs(videoCodecOptions, 'mp4')
    expect(result.has('libvpx-vp9')).toBe(true)
    expect(result.has('libx264')).toBe(false)
  })

  it('returns empty set for MKV', () => {
    const result = incompatibleVideoCodecs(videoCodecOptions, 'mkv')
    expect(result.size).toBe(0)
  })
})

describe('incompatibleContainers', () => {
  it('returns WebM as incompatible with H.264', () => {
    const result = incompatibleContainers(containerOptions, 'libx264')
    expect(result.has('webm')).toBe(true)
    expect(result.has('mp4')).toBe(false)
  })
})

describe('audioCodecsForContainer', () => {
  it('filters to WebM-compatible codecs', () => {
    const result = audioCodecsForContainer(audioCodecOpts, 'webm')
    const values = result.map((o) => o.value)
    expect(values).toEqual(['opus', 'vorbis'])
  })

  it('includes DTS and TrueHD for MKV', () => {
    const result = audioCodecsForContainer(audioCodecOpts, 'mkv')
    const values = result.map((o) => o.value)
    expect(values).toContain('dts')
    expect(values).toContain('truehd')
  })
})

describe('bestContainerForCodec', () => {
  it('keeps current container if compatible', () => {
    expect(bestContainerForCodec('libx264', 'mp4', containerOptions)).toBe('mp4')
  })

  it('switches to first compatible container if current is incompatible', () => {
    expect(bestContainerForCodec('libvpx-vp9', 'mp4', containerOptions)).toBe('mkv')
  })
})

describe('bestCodecForContainer', () => {
  it('keeps current codec if compatible', () => {
    expect(bestCodecForContainer('mp4', 'libx264', videoCodecOptions)).toBe('libx264')
  })

  it('switches to first compatible codec if current is incompatible', () => {
    expect(bestCodecForContainer('webm', 'libx264', videoCodecOptions)).toBe('libvpx-vp9')
  })
})

describe('bestAudioCodecForContainer', () => {
  it('keeps current audio codec if compatible', () => {
    expect(bestAudioCodecForContainer('mkv', 'flac', audioCodecOpts)).toBe('flac')
  })

  it('switches to first compatible audio codec if current is incompatible', () => {
    expect(bestAudioCodecForContainer('webm', 'aac', audioCodecOpts)).toBe('opus')
  })
})
