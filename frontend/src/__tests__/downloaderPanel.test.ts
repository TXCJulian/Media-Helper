import { describe, expect, it } from 'vitest'
import { parseUrls } from '@/components/DownloaderPanel'

describe('parseUrls', () => {
  it('returns a single url unchanged', () => {
    expect(parseUrls('https://example.com/a')).toEqual(['https://example.com/a'])
  })

  it('splits one url per line and drops blanks', () => {
    expect(parseUrls('https://a\n\n  https://b  \n')).toEqual(['https://a', 'https://b'])
  })

  it('returns an empty array for empty input', () => {
    expect(parseUrls('   \n  ')).toEqual([])
  })
})
