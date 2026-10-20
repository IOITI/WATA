import { formatDay, formatNumber, formatPercent } from '@/lib/format'
import type { BacktestPaper } from '@/lib/types'

/** What a rule's days traded on paper say, beside its reference run. Pure. */

export interface PaperLine {
  /** "4 days, +1.20 %" */
  total: string
  /** "Last, Mon 19 Oct: +0.40 %" */
  last: string | null
  reference: string
  /** How many of the days with a result fell outside the reference's range. */
  outside: number
  tone: 'ok' | 'warning' | 'neutral'
}

export function paperLine(entry: BacktestPaper['rules'][string] | undefined, mode: string): PaperLine | null {
  if (!entry || entry.days.length === 0) {
    return mode === 'shadow'
      ? { total: 'Each finished day, once the market is closed', last: null, reference: '', outside: 0, tone: 'neutral' }
      : null
  }
  const results = entry.days.filter((day) => day.day_percent !== null)
  const total = results.reduce((sum, day) => sum + (day.day_percent ?? 0), 0)
  const last = results[0]
  const outside = results.filter((day) => day.outside).length
  const reference = entry.reference
  let referenceText = 'No reference run: set one from a run made from this rule.'
  if (reference) {
    referenceText =
      reference.low === null
        ? `Its reference has ${reference.days} day${reference.days === 1 ? '' : 's'}: too few for a range.`
        : `Its reference${reference.name ? ` (${reference.name})` : ''} made ${formatPercent(reference.low, true)} to ${formatPercent(reference.high, true)} on 90 % of its days.`
  }
  return {
    total: `${results.length} day${results.length === 1 ? '' : 's'}, ${formatPercent(total, true)}`,
    last: last ? `Last, ${formatDay(last.day, false)}: ${formatPercent(last.day_percent, true)}${last.trades !== null ? ` in ${formatNumber(last.trades)} trade${last.trades === 1 ? '' : 's'}` : ''}` : null,
    reference: referenceText,
    outside,
    tone: !reference || reference.low === null ? 'neutral' : outside > 0 ? 'warning' : 'ok',
  }
}
