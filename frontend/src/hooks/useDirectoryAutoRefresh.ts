import { useEffect, useRef } from 'react'

export function useDirectoryAutoRefresh(refresh: () => void, intervalMs = 30_000): void {
  const latestRefresh = useRef(refresh)

  useEffect(() => {
    latestRefresh.current = refresh
  }, [refresh])

  useEffect(() => {
    const interval = window.setInterval(() => {
      void Promise.resolve(latestRefresh.current()).catch(() => {})
    }, intervalMs)
    return () => window.clearInterval(interval)
  }, [intervalMs])
}
