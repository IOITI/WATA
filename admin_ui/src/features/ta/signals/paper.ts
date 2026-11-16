import { formatDay, formatNumber, formatPercent } from 'ioiti-ui'

import { paperVerdict } from '@/features/edgeWatch/edgeWatch'
import { reportLine } from '@/features/backtests/paperModel'
import type { BacktestPaper, EdgeOwner } from '@/lib/types'

/** What a rule's days traded on paper say, and what Edge Watch says of its paper trades against its reference. Pure. */

export interface PaperLine {
  /** "4 days, +1.20 %" */
  total: string
  /** "Since 14 Oct: +1.18 % in 9 trades": its report, one run of every day since it was made; null before the first. */
  report: string | null
  /** "Last, Mon 19 Oct: +0.40 %" */
  last: string | null
  /** Edge Watch's verdict on its paper trades ("Worth watching after 24 trades, −3.10 %"), or how to get one. */
  reference: string
  tone: 'ok' | 'warning' | 'neutral'
}

const TONE: Record<string, PaperLine['tone']> = { success: 'ok', warning: 'warning', danger: 'warning', neutral: 'neutral' }

export function paperLine(
  entry: BacktestPaper['rules'][string] | undefined,
  mode: string,
  /** Its Edge Watch (GET /api/edge-watch's rules): undefined while not read, or when it has no reference. */
  edge?: EdgeOwner,
): PaperLine | null {
  const report = entry?.report ? reportLine(entry.report) : null
  if (!entry || (entry.days.length === 0 && !report)) {
    return mode === 'shadow'
      ? { total: 'Each finished day, once the market is closed', report: null, last: null, reference: '', tone: 'neutral' }
      : null
  }
  const results = entry.days.filter((day) => day.day_percent !== null)
  const total = results.reduce((sum, day) => sum + (day.day_percent ?? 0), 0)
  const last = results[0]
  const verdict = paperVerdict(edge)
  const reference = verdict
    ? `Edge Watch: ${verdict.text}.`
    : entry.reference
      ? `Its reference${entry.reference.name ? ` (${entry.reference.name})` : ''}: Edge Watch judges its paper trades after it.`
      : 'No reference run: set one from a run made from this rule.'
  return {
    total: `${results.length} day${results.length === 1 ? '' : 's'}, ${formatPercent(total, true)}`,
    report,
    last: last ? `Last, ${formatDay(last.day, false)}: ${formatPercent(last.day_percent, true)}${last.trades !== null ? ` in ${formatNumber(last.trades)} trade${last.trades === 1 ? '' : 's'}` : ''}` : null,
    reference,
    tone: verdict ? TONE[verdict.tone] ?? 'neutral' : 'neutral',
  }
}
