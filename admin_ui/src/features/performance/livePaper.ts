// What the Performance page says of the live settings' paper days (src/admin/paper.py, live_drift.py): the
// signals WATA received replayed each finished day under the live settings, beside the real day and the daily
// range of the replay chosen as the live reference. Pure.

import { formatPercent } from 'ioiti-ui'

import type { BacktestPaperReference, LivePaperDay } from '@/lib/types'

/** What the reference is, or how to choose one. */
export function referenceText(reference: BacktestPaperReference | null): string {
  if (!reference) {
    return 'No live reference yet: on the page of a replay of the signals WATA received, “Set as the live reference” makes its daily range the one each day is set against.'
  }
  if (reference.low === null || reference.high === null) {
    return `${reference.name ?? 'The live reference'} has ${reference.days} day${reference.days === 1 ? '' : 's'} with a result: 5 are needed for a range.`
  }
  return `Set against ${reference.name ?? 'the live reference'}: its days made ${formatPercent(reference.low, true)} to ${formatPercent(reference.high, true)} (5 % to 95 % of ${reference.days} days).`
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

/** The days outside the reference's range, newest first: what the warning rests on. */
export function outsideDays(days: LivePaperDay[]): string[] {
  return days.filter((day) => day.real_outside || day.paper_outside).map((day) => day.day)
}
