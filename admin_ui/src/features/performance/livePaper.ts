// What the Performance page says of the live settings' paper days (src/admin/paper.py, live_drift.py live_days): the
// signals WATA received replayed each finished day under the live settings, beside the real day. Whether they are
// what the live reference led to expect is Edge Watch's (EdgeWatchPanel), trade by trade. Pure.

import type { BacktestPaperReference, LivePaperDay } from '@/lib/types'

/** What the reference is, or how to choose one. */
export function referenceText(reference: BacktestPaperReference | null): string {
  if (!reference) {
    return 'No live reference yet: on the page of a replay of the signals WATA received, “Set as the live reference” makes it the backtest Edge Watch judges the live trades against.'
  }
  return `The live reference: ${reference.name ?? 'a replay of the signals received'}, ${reference.days} day${reference.days === 1 ? '' : 's'}. Edge Watch, above, judges the live trades against it.`
}

/** A day's paper run as a word when it has no result: not run yet, running, failed, or no entry signal. */
export function paperState(day: LivePaperDay): string | null {
  if (day.paper_percent !== null && day.paper_state !== 'no_entry') return null
  switch (day.paper_state) {
    case 'no_entry':
      return 'No entry signal'
    case 'queued':
    case 'running':
      return 'Running'
    case 'failed':
    case 'cancelled':
    case 'interrupted':
      return 'Not traded'
    case null:
      return '—'
    default:
      return null
  }
}
