// @vitest-environment node

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import viteConfig from '../../vite.config'

function nginxLocation(config: string, path: string): string {
  const marker = `location ${path} {`
  const start = config.indexOf(marker)
  if (start < 0) return ''

  let depth = 0
  for (let index = start; index < config.length; index += 1) {
    if (config[index] === '{') depth += 1
    if (config[index] === '}') {
      depth -= 1
      if (depth === 0) return config.slice(start, index + 1)
    }
  }
  return ''
}

describe('encoder public routing', () => {
  it('maps the API-prefixed client path through the development proxy', () => {
    const proxy = viteConfig.server?.proxy as Record<string, { target?: string }> | undefined

    expect(proxy?.['/api/encoder']).toMatchObject({
      target: 'http://localhost:8000',
      changeOrigin: true,
    })
  })

  it('passes the API-prefixed path through Nginx with SSE streaming enabled', () => {
    const config = readFileSync(new URL('../../nginx-app.conf', import.meta.url), 'utf8')
    const location = nginxLocation(config, '/api/encoder/')

    expect(location).toContain('proxy_pass http://__BACKEND_HOST__:__BACKEND_PORT__/api/encoder/;')
    expect(location).toContain('proxy_buffering off;')
    expect(location).toContain('proxy_cache off;')
    expect(location).toContain('proxy_read_timeout 1800s;')
    expect(location).toContain("proxy_set_header Connection '';")
    expect(location).toContain('chunked_transfer_encoding off;')
  })
})
