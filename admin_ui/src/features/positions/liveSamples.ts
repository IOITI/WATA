/**
 * The live values the position monitor published for each open position, as this tab read them. The server keeps
 * only the latest (`position_live_value`), so the line a chart draws starts when the tab first read it. They are
 * kept in `sessionStorage` (a reload keeps them, another tab has its own), half an hour per position at most, and
 * a position no longer open is forgotten.
 */

import type { Position } from '@/lib/types'

export interface LiveSample {
  /** When the monitor saw it (ms, the server's clock). */
  t: number
  /** Its performance then, in percent. */
  v: number
}

type Samples = Record<string, LiveSample[]>
type Listener = (positionId: string, sample: LiveSample) => void

const KEY = 'wata.live-samples'
export const KEEP_MS = 30 * 60_000

let memory: Samples | null = null
const listeners = new Set<Listener>()

function load(): Samples {
  if (memory) return memory
  try {
    const raw = sessionStorage.getItem(KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : {}
    memory = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Samples) : {}
  } catch {
    // No storage (a private window, blocked site data): kept for this page only.
    memory = {}
  }
  return memory
}

function save(samples: Samples): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(samples))
  } catch {
    // As above: what is in memory still draws the lines.
  }
}

/**
 * Adds what the overview says of the open positions: a value once per moment the monitor saw it (the same reading
 * read twice is one sample), the older than `KEEP_MS` dropped.
 */
export function recordLiveValues(positions: Position[], now: number = Date.now()): void {
  const samples = load()
  const open = new Set(positions.map((position) => position.position_id))
  for (const id of Object.keys(samples)) if (!open.has(id)) delete samples[id]

  const added: [string, LiveSample][] = []
  for (const position of positions) {
    const value = position.live_performance_percent
    const seen = position.live_observed_at ? Date.parse(position.live_observed_at) : Number.NaN
    if (value == null || !Number.isFinite(value) || !Number.isFinite(seen)) continue
    const kept = (samples[position.position_id] ?? []).filter((sample) => sample.t >= now - KEEP_MS)
    if (kept.length > 0 && kept[kept.length - 1].t >= seen) {
      samples[position.position_id] = kept
      continue
    }
    const sample = { t: seen, v: value }
    samples[position.position_id] = [...kept, sample]
    added.push([position.position_id, sample])
  }
  save(samples)
  for (const [id, sample] of added) for (const listener of listeners) listener(id, sample)
}

/** What this tab kept of a position's live values, oldest first. */
export function liveSamples(positionId: string): LiveSample[] {
  return [...(load()[positionId] ?? [])]
}

/** Called with each new sample, until the returned function is called. */
export function onLiveSample(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** For the tests: forgets what was kept in memory, so that the next read is the storage's. */
export function forgetLiveSamples(): void {
  memory = null
}
