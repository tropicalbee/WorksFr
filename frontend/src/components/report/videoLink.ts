const YOUTUBE_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'])

/** Formats whole seconds as m:ss, or h:mm:ss from one hour. */
export function formatTimestamp(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = String(seconds % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

/** Returns the YouTube URL opened at the given second, or null when the source is not a YouTube link. */
export function videoLinkAt(source: string, seconds: number): string | null {
  let url: URL
  try {
    url = new URL(source)
  } catch {
    return null
  }
  if (!/^https?:$/.test(url.protocol) || !YOUTUBE_HOSTS.has(url.hostname.toLowerCase())) return null
  url.searchParams.set('t', `${Math.max(0, Math.floor(seconds))}s`)
  return url.toString()
}
