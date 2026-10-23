import { describe, expect, it } from 'vitest'

import type { JournalEntry } from '@/lib/types'

import { journalFunnel } from './journalFunnel'

function entry(action: string, outcome: string, decidedAt: string): JournalEntry {
  return {
    signal_uuid: `${action}-${outcome}-${decidedAt}`,
    action,
    indice: 'US100',
    confidence: null,
    signal_timestamp: decidedAt,
    received_timestamp: decidedAt,
    decided_at: decidedAt,
    outcome,
    reason: null,
    position_id: null,
    order_id: null,
  }
}

describe('where the entry signals the trader decided went', () => {
  it('counts the entries alone, from the refused to the bought, over the days they span', () => {
    const funnel = journalFunnel([
      entry('long', 'executed', '2026-10-23T13:00:00Z'),
      entry('short', 'refused', '2026-10-22T09:00:00Z'),
      entry('long', 'refused', '2026-10-21T15:00:00Z'),
      entry('long', 'failed', '2026-10-21T14:00:00Z'),
      entry('close-position', 'closed', '2026-10-20T20:00:00Z'),
    ])

    expect(funnel).toEqual({
      stages: [
        { label: 'Entry signals decided', value: 4, dropLabel: 'refused by a rule of the trader' },
        { label: 'Passed the rules', value: 2, dropLabel: 'failed or not bought' },
        { label: 'Bought', value: 1 },
      ],
      from: '2026-10-21T14:00:00Z',
      to: '2026-10-23T13:00:00Z',
    })
  })

  it('is nothing without an entry signal', () => {
    expect(journalFunnel([entry('close-position', 'ignored', '2026-10-20T20:00:00Z')])).toBeNull()
    expect(journalFunnel([])).toBeNull()
  })
})
