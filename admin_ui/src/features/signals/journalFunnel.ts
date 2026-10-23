/**
 * Where the entry signals the trader decided went: refused by one of its rules, failed (no turbo found, Saxo
 * refusing the order...), or bought. Over the journal's entries the page read, with the days they span: the
 * journal is read newest first, so its oldest entry says from when.
 */

import type { FunnelStage } from '@/components/ui/funnel-chart'
import type { JournalEntry } from '@/lib/types'

export interface JournalFunnel {
  stages: FunnelStage[]
  /** The first and the last decision counted (ISO), oldest first. */
  from: string | null
  to: string | null
}

const ENTRIES = new Set(['long', 'short'])

export function journalFunnel(entries: JournalEntry[]): JournalFunnel | null {
  const decided = entries.filter((entry) => entry.action !== null && ENTRIES.has(entry.action))
  if (decided.length === 0) return null
  const refused = decided.filter((entry) => entry.outcome === 'refused').length
  const bought = decided.filter((entry) => entry.outcome === 'executed').length
  const times = decided.map((entry) => entry.decided_at).filter((at): at is string => !!at).sort()
  return {
    stages: [
      { label: 'Entry signals decided', value: decided.length, dropLabel: 'refused by a rule of the trader' },
      { label: 'Passed the rules', value: decided.length - refused, dropLabel: 'failed or not bought' },
      { label: 'Bought', value: bought },
    ],
    from: times[0] ?? null,
    to: times[times.length - 1] ?? null,
  }
}
