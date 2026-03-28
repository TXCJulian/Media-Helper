import { API_BASE, assertAuthenticated } from './api'

interface SSECallbacks {
  onProgress: (data: string) => void
  onError: (data: string) => void
  onDone: (data: string) => void
}

export function connectSSE(
  path: string,
  params: Record<string, string>,
  callbacks: SSECallbacks,
): () => void {
  const url = new URL(path, API_BASE)

  const controller = new AbortController()

  const formData = new FormData()
  for (const [k, v] of Object.entries(params)) {
    if (v) formData.append(k, v)
  }

  ;(async () => {
    let response: Response
    try {
      response = await fetch(url.toString(), {
        method: 'POST',
        body: formData,
        signal: controller.signal,
        credentials: 'include',
      })
    } catch {
      if (!controller.signal.aborted) {
        callbacks.onError('Connection failed')
      }
      return
    }

    try {
      assertAuthenticated(response)
    } catch {
      callbacks.onError('Session expired')
      return
    }

    if (!response.ok) {
      callbacks.onError(`HTTP ${response.status}: ${response.statusText}`)
      return
    }

    const reader = response.body?.getReader()
    if (!reader) {
      callbacks.onError('No response stream')
      return
    }

    const decoder = new TextDecoder()
    let buffer = ''
    let receivedDone = false

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Parse SSE events from buffer
        const parts = buffer.split('\n\n')
        // Keep the last incomplete part in buffer
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          if (!part.trim()) continue

          let eventType = 'message'
          const dataLines: string[] = []

          for (const line of part.split('\n')) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7)
            } else if (line.startsWith('data: ')) {
              dataLines.push(line.slice(6))
            }
          }

          const data = dataLines.join('\n')

          if (!data) continue

          switch (eventType) {
            case 'progress':
              callbacks.onProgress(data)
              break
            case 'error_msg':
              callbacks.onError(data)
              break
            case 'done':
              receivedDone = true
              callbacks.onDone(data)
              return
          }
        }
      }

      // Stream ended without a done event
      if (!receivedDone && !controller.signal.aborted) {
        callbacks.onError('Connection lost - stream ended unexpectedly')
      }
    } catch {
      if (!controller.signal.aborted) {
        callbacks.onError('Connection lost')
      }
    }
  })()

  return () => controller.abort()
}
