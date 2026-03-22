/**
 * Codec / container compatibility rules.
 *
 * All selectors always show every option. When the user changes one field the
 * *other* fields auto-correct to the nearest compatible value, so the last
 * thing the user touched always wins.
 */

type Option = { label: string; value: string }

// ── Video codecs allowed per container ──────────────────────────────────────
const containerVideoCodecs: Record<string, Set<string>> = {
  mp4: new Set(['libx264', 'libx265', 'libsvtav1']),
  mkv: new Set(['libx264', 'libx265', 'libvpx-vp9', 'libsvtav1']),
  webm: new Set(['libvpx-vp9', 'libsvtav1']),
  mov: new Set(['libx264', 'libx265']),
}

// ── Audio codecs allowed per container (for re-encoding targets) ────────────
const containerAudioCodecs: Record<string, Set<string>> = {
  mp4: new Set(['aac', 'ac3', 'mp3', 'opus']),
  mkv: new Set(['aac', 'ac3', 'dts', 'truehd', 'flac', 'opus', 'vorbis', 'mp3']),
  webm: new Set(['opus', 'vorbis']),
  mov: new Set(['aac', 'ac3', 'flac', 'mp3']),
  // Audio-only containers
  mka: new Set(['aac', 'ac3', 'dts', 'truehd', 'flac', 'opus', 'vorbis', 'mp3']),
  ogg: new Set(['opus', 'vorbis', 'flac']),
  mp3: new Set(['mp3']),
  flac: new Set(['flac']),
}

// ── Containers that reject stream-copy (passthru) for specific codecs ──────
// Passthru copies the bitstream as-is. Most containers (especially MKV/MKA)
// accept virtually any codec via stream copy. Only list containers with real
// muxing restrictions here — if a container isn't listed, passthru is allowed.
const containerPassthruBlacklist: Record<string, Set<string>> = {
  webm: new Set(['aac', 'ac3', 'mp3', 'flac', 'truehd', 'dts', 'pcm_s16le', 'pcm_s24le']),
  ogg: new Set(['aac', 'ac3', 'mp3', 'truehd', 'dts']),
  mp3: new Set(['aac', 'ac3', 'flac', 'opus', 'vorbis', 'truehd', 'dts']),
  flac: new Set(['aac', 'ac3', 'mp3', 'opus', 'vorbis', 'truehd', 'dts']),
}

// ── Containers allowed per video codec (inverse of containerVideoCodecs) ────
const videoCodecContainers: Record<string, Set<string>> = {}
for (const [ctr, codecs] of Object.entries(containerVideoCodecs)) {
  for (const codec of codecs) {
    ;(videoCodecContainers[codec] ??= new Set()).add(ctr)
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/** Check whether a video codec is compatible with a container. */
export function isVideoCodecCompatible(videoCodec: string, container: string): boolean {
  const allowed = containerVideoCodecs[container]
  return !allowed || allowed.has(videoCodec)
}

/** Check whether an audio codec is compatible with a container (for re-encoding). */
export function isAudioCodecCompatible(audioCodec: string, container: string): boolean {
  const allowed = containerAudioCodecs[container]
  return !allowed || allowed.has(audioCodec)
}

/** Check whether a source audio codec can be stream-copied (passthru) into a container. */
export function isPassthruCompatible(sourceCodec: string, container: string): boolean {
  const blacklist = containerPassthruBlacklist[container]
  return !blacklist || !blacklist.has(sourceCodec.toLowerCase())
}

/** Return the set of video codec values incompatible with `container`. */
export function incompatibleVideoCodecs(allOptions: Option[], container: string): Set<string> {
  const allowed = containerVideoCodecs[container]
  if (!allowed) return new Set()
  return new Set(allOptions.filter((o) => !allowed.has(o.value)).map((o) => o.value))
}

/** Return the set of container values incompatible with `videoCodec`. */
export function incompatibleContainers(allOptions: Option[], videoCodec: string): Set<string> {
  const allowed = videoCodecContainers[videoCodec]
  if (!allowed) return new Set()
  return new Set(allOptions.filter((o) => !allowed.has(o.value)).map((o) => o.value))
}

/** Filter audio codec options to those compatible with `container`. */
export function audioCodecsForContainer(allOptions: Option[], container: string): Option[] {
  const allowed = containerAudioCodecs[container]
  if (!allowed) return allOptions
  return allOptions.filter((o) => allowed.has(o.value))
}

/**
 * Pick the best compatible value from `options` for the current state.
 * Returns `current` if it's already compatible, otherwise the first
 * compatible option, otherwise `current` unchanged (shouldn't happen).
 */
function bestMatch(current: string, options: Option[], allowed: Set<string> | undefined): string {
  if (!allowed || allowed.has(current)) return current
  const first = options.find((o) => allowed.has(o.value))
  return first?.value ?? current
}

/**
 * Given that the user just picked `videoCodec`, return the best container.
 * Keeps `currentContainer` if compatible, otherwise picks the first compatible one.
 */
export function bestContainerForCodec(
  videoCodec: string,
  currentContainer: string,
  containerOptions: Option[],
): string {
  const allowed = videoCodecContainers[videoCodec]
  return bestMatch(currentContainer, containerOptions, allowed)
}

/**
 * Given that the user just picked `container`, return the best video codec.
 * Keeps `currentCodec` if compatible, otherwise picks the first compatible one.
 */
export function bestCodecForContainer(
  container: string,
  currentCodec: string,
  codecOptions: Option[],
): string {
  const allowed = containerVideoCodecs[container]
  return bestMatch(currentCodec, codecOptions, allowed)
}

/**
 * Given that the container changed, return the best audio codec.
 * Keeps `current` if compatible, otherwise picks the first compatible one.
 */
export function bestAudioCodecForContainer(
  container: string,
  current: string,
  audioOptions: Option[],
): string {
  const allowed = containerAudioCodecs[container]
  return bestMatch(current, audioOptions, allowed)
}
