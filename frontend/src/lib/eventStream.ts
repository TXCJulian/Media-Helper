import { API_BASE, assertAuthenticated } from './http'
import { parseSSEChunk } from './sse'

interface EventStreamOptions {
  onStateChange?: (connected: boolean) => void
  /** Base reconnect delay in ms; doubles up to 30s while the server is down.
   *  The backoff only resets once a connection actually yields a frame — a
   *  connection that is accepted and then closes without ever sending one
   *  (e.g. a server mid-restart) counts as a failed attempt, not a success. */
  retryMs?: number
}

/**
 * Hold a long-lived GET SSE connection, reconnecting until closed.
 *
 * Unlike `connectSSE`, this never terminates on a `done` event — the server
 * stream is a continuous state feed, and every reconnect replays a full
 * snapshot, so a client that misses deltas still converges.
 */
export function openEventStream(
  path: string,
  onEvent: (data: string) => void,
  options: EventStreamOptions = {},
): () => void {
  const baseRetry = options.retryMs ?? 1000
  let closed = false
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let attempt = 0

  const setConnected = (connected: boolean) => options.onStateChange?.(connected)

  const scheduleReconnect = () => {
    if (closed) return
    const delay = Math.min(baseRetry * 2 ** attempt, 30_000)
    attempt += 1
    timer = setTimeout(connect, delay)
  }

  async function connect(): Promise<void> {
    if (closed) return
    controller = new AbortController()

    let response: Response
    try {
      response = await fetch(new URL(path, API_BASE).toString(), {
        signal: controller.signal,
        credentials: 'include',
        headers: { Accept: 'text/event-stream' },
      })
    } catch {
      setConnected(false)
      scheduleReconnect()
      return
    }

    // An expired session is not transient: retrying cannot fix it, and doing so
    // would leave the panel stuck on "Reconnecting..." while quietly hammering
    // the server. Hand it to the app-wide auth-expired flow and stop.
    if (response.status === 401) {
      closed = true
      setConnected(false)
      try {
        assertAuthenticated(response)
      } catch {
        // assertAuthenticated dispatches `auth:expired` before throwing; the
        // throw carries no information this caller can act on.
      }
      return
    }

    if (!response.ok || !response.body) {
      setConnected(false)
      scheduleReconnect()
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let receivedFrame = false

    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const { events, rest } = parseSSEChunk(buffer)
        buffer = rest
        for (const frame of events) {
          // Only a connection that actually yields a frame (the server sends
          // a full snapshot immediately on connect) counts as "working" —
          // reset backoff and report connected here, not merely on HTTP 200.
          // Otherwise a server that accepts and immediately drops the
          // connection (e.g. mid-restart) would reset `attempt` every time
          // and retry at a flat rate instead of backing off.
          if (!receivedFrame) {
            receivedFrame = true
            attempt = 0
            setConnected(true)
          }
          onEvent(frame.data)
        }
      }
    } catch {
      // Fall through to the reconnect path below.
    }

    setConnected(false)
    scheduleReconnect()
  }

  void connect()

  return () => {
    closed = true
    if (timer) clearTimeout(timer)
    controller?.abort()
    setConnected(false)
  }
}
