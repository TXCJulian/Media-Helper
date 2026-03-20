export function encodeCutterFileId(source: string, path: string, jobId = '', base = ''): string {
  const bytes = new TextEncoder().encode(`${source}|${jobId}|${base}|${path}`)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
