import { describe, it, expect } from 'vitest'
import { encodeCutterFileId } from '@/lib/cutterFileId'

function decode(encoded: string): string {
  const padded = encoded + '='.repeat((4 - (encoded.length % 4)) % 4)
  const bin = atob(padded.replace(/-/g, '+').replace(/_/g, '/'))
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)))
}

describe('encodeCutterFileId', () => {
  it('encodes source, job id, base, and path as a four-part payload', () => {
    const fileId = encodeCutterFileId('server', 'Movies/Test.mkv', 'job-123', 'media')
    expect(decode(fileId)).toBe('server|job-123|media|Movies/Test.mkv')
  })

  it('keeps upload payload compatible with empty base label', () => {
    const fileId = encodeCutterFileId('upload', 'clip.mp4', 'job-456')
    expect(decode(fileId)).toBe('upload|job-456||clip.mp4')
  })
})
