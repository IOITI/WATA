import type {
  LiveCheck,
  LiveCheckAgreement,
  LiveCheckCalibration,
  LiveCheckDayLine,
  LiveCheckSegment,
  LiveCheckSignal,
  LiveCheckSignalSide,
  LiveCheckStrategy,
  LiveCheckTotals,
  LiveCheckTrack,
  LiveCheckTrade,
  LiveCheckTradeRow,
} from '@/lib/types'

import type { DrawingLabel } from '@luxalgo/vela'

import { pointsText, secondsText } from '@/features/positions/executionLedger'

import { barLocator, runLayer } from '../chart/runDayModel'
import type { LayerOf } from '../chart/runDayModel'

/**
 * What the rule page's "Live, paper and backtest" says (src/admin/live_check.py): a rule's real trades beside its paper
 * days, the backtest configuration linked to it and that configuration's calibrated run. Results are the trades'
 * results summed, in points of the turbo: the bridge adds up from one track to the next.
 */

export const TRACKS: LiveCheckTrack[] = ['backtest', 'calibrated', 'paper', 'live']

export const TRACK_LABEL: Record<LiveCheckTrack, string> = {
  backtest: 'Backtest',
  calibrated: 'Calibrated',
  paper: 'Paper',
  live: 'Live',
}

export const TRACK_HINT: Record<LiveCheckTrack, string> = {
  backtest: 'The configuration linked to the rule: its script replayed on the recorded bars.',
  calibrated: 'The same configuration made again each evening with what the live trades measured.',
  paper: 'The signals the engine raised, traded as WATA would have, from the account’s value that morning while live.',
  live: 'What WATA really traded on Saxo from the rule’s signals.',
}

export { pointsText, secondsText }

/** The address of a day of the comparison. */
export function liveCheckDayHref(ruleId: string, day: string): string {
  return `/ta/signals/${encodeURIComponent(ruleId)}/live/${day}`
}

export interface DayRow {
  day: string
  live: boolean
  cells: Record<LiveCheckTrack, { trades: number; sum: number } | null>
  gap: { against: LiveCheckTrack; points: number } | null
}

/** A day of the table: each track's trades and summed result there, and the gap to the backtest. */
export function dayRow(line: LiveCheckDayLine): DayRow {
  const cells = Object.fromEntries(
    TRACKS.map((track) => {
      const figures = line.figures[track]
      return [track, figures ? { trades: figures.trades, sum: figures.sum } : null]
    }),
  ) as DayRow['cells']
  return { day: line.day, live: line.present.includes('live'), cells, gap: line.gap }
}

/** The tracks there on at least one day: the table's columns. */
export function shownTracks(lines: LiveCheckDayLine[]): LiveCheckTrack[] {
  return TRACKS.filter((track) => lines.some((line) => line.present.includes(track)))
}

export interface BridgeBar {
  id: string
  label: string
  points: number
  trades: number
  /** Its length beside the longest of the segment, 0 to 1. */
  share: number
}

/** A segment of the bridge as signed bars, the longest one whole. */
export function bridgeBars(segment: LiveCheckSegment): BridgeBar[] {
  const longest = Math.max(0, ...segment.buckets.map((bucket) => Math.abs(bucket.points)))
  return segment.buckets.map((bucket) => ({
    id: bucket.id,
    label: bucket.label,
    points: bucket.points,
    trades: bucket.trades,
    share: longest > 0 ? Math.abs(bucket.points) / longest : 0,
  }))
}

/** A segment in one sentence: over the days both tracks have, from what to what, and where most of it went. */
export function segmentSaid(segment: LiveCheckSegment): string {
  const moved = segment.end.sum - segment.start.sum
  const over = segment.days !== undefined ? `Over the ${segment.days} day${segment.days === 1 ? '' : 's'} both have: ` : ''
  const start = over ? TRACK_LABEL[segment.from].toLowerCase() : TRACK_LABEL[segment.from]
  const from = `${over}${start} ${pointsText(segment.start.sum)} in ${tradesWord(segment.start.trades)}`
  const to = `${TRACK_LABEL[segment.to].toLowerCase()} ${pointsText(segment.end.sum)} in ${tradesWord(segment.end.trades)}`
  if (segment.buckets.length === 0) return `${from}, ${to}: nothing moved.`
  const biggest = [...segment.buckets].sort((one, other) => Math.abs(other.points) - Math.abs(one.points))[0]
  return `${from}, ${to} (${pointsText(moved)}): most of it ${biggest.label.toLowerCase()}, ${pointsText(biggest.points)} over ${tradesWord(biggest.trades)}.`
}

function tradesWord(count: number): string {
  return `${count} trade${count === 1 ? '' : 's'}`
}

/** How far the replayed script raised the engine's signals, said. */
export function agreementSaid(agreement: (LiveCheckAgreement & { raised?: number; same_bar_percent?: number | null }) | null): string | null {
  if (!agreement) return null
  const raised = agreement.raised ?? agreement.same_bar + agreement.other_bar + agreement.not_replayed
  if (raised === 0 && agreement.not_raised === 0) return 'No entry signal on either side.'
  const parts = [
    `The backtest raised ${agreement.same_bar} of the engine’s ${raised} entry signal${raised === 1 ? '' : 's'} at the same bar`
      + (agreement.same_bar_percent != null ? ` (${agreement.same_bar_percent.toFixed(1)} %)` : ''),
  ]
  if (agreement.other_bar) parts.push(`${agreement.other_bar} at another bar`)
  if (agreement.not_replayed) parts.push(`${agreement.not_replayed} not at all`)
  if (agreement.not_raised) parts.push(`and ${agreement.not_raised} the engine never raised`)
  return `${parts.join(', ')}.`
}

/** Whether the linked configuration plays the rule's strategy, said. */
export function strategySaid(strategy: LiveCheckStrategy | null): { tone: 'success' | 'danger' | 'neutral'; text: string } {
  if (!strategy) return { tone: 'neutral', text: 'Not checked: the configuration has no finished run of its own.' }
  if (!strategy.same) {
    return { tone: 'danger', text: `Not the rule’s strategy: ${strategy.checks.filter((check) => check.state === 'fail').map((check) => check.detail).join('; ')}.` }
  }
  const notes = strategy.checks.filter((check) => check.state === 'note').map((check) => check.detail)
  return { tone: 'success', text: `The rule’s strategy${notes.length > 0 ? `: ${notes.join('; ')}` : ': the same script, chart, alerts, history and inputs'}.` }
}

/** How much of the gap between the backtest and live the calibrated run closes, said. */
export function calibratedSaid(calibrated: LiveCheckTotals['calibrated']): string | null {
  if (!calibrated) return null
  const days = `${calibrated.days} live day${calibrated.days === 1 ? '' : 's'}`
  const figures = `backtest ${pointsText(calibrated.backtest.sum)}, calibrated ${pointsText(calibrated.calibrated.sum)}, live ${pointsText(calibrated.live.sum)}`
  if (calibrated.closed_percent == null) return `Over ${days}: ${figures}. No gap between the backtest and live to close.`
  return `Over ${days}: ${figures}. The calibrated run closes ${calibrated.closed_percent.toFixed(0)} % of the gap between the backtest and live.`
}

export interface CalibrationRow {
  name: string
  label: string
  backtest: string
  measured: string
  applied: boolean
}

function valueText(name: string, value: number | string | null | undefined): string {
  if (value == null) return '—'
  if (name === 'signal_delay_seconds') return secondsText(Number(value))
  if (name === 'capital') return `${Number(value).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`
  return value === 'measured' ? 'Drawn from WATA’s orders' : value === 'fixed' ? 'Fixed' : String(value)
}

/** What the calibrated run changes of its backtest, row by row: the backtest's value, what was measured, whether it is taken. */
export function calibrationRows(now: LiveCheckCalibration): CalibrationRow[] {
  const delay = now.measured.signal_delay_seconds
  const measuredDelay = delay.count > 0 && delay.median != null
    ? `${secondsText(delay.median)} (median of ${delay.count}, 90 % under ${secondsText(delay.p90)})`
    : 'No live entry yet'
  const measures = now.orders?.measures ?? {}
  const fewest = Object.values(measures).length > 0 ? Math.min(...Object.values(measures)) : 0
  return [
    {
      name: 'signal_delay_seconds',
      label: 'From the bar’s close to the order',
      backtest: valueText('signal_delay_seconds', now.template.signal_delay_seconds),
      measured: measuredDelay,
      applied: now.values.signal_delay_seconds !== undefined,
    },
    {
      name: 'execution',
      label: 'Fills',
      backtest: valueText('execution', now.template.execution ?? 'fixed'),
      measured: Object.keys(measures).length > 0 ? `${fewest} order${fewest === 1 ? '' : 's'} measured at least of each kind` : 'Not measured',
      applied: now.values.execution !== undefined,
    },
    {
      name: 'capital',
      label: 'The account',
      backtest: valueText('capital', now.template.capital),
      measured: valueText('capital', now.values.capital),
      applied: now.values.capital !== undefined,
    },
  ]
}

/** The entry and exit slippage the live trades paid, said. */
export function slippageSaid(now: LiveCheckCalibration): string | null {
  const entry = now.measured.entry_slippage_percent
  const exit = now.measured.exit_slippage_percent
  if (entry.count === 0 && exit.count === 0) return null
  const part = (label: string, found: typeof entry) =>
    found.count > 0 && found.median != null ? `${label} ${pointsText(found.median)} at the median of ${found.count}` : null
  return [part('Entries paid', entry), part('exits gave', exit)].filter(Boolean).join('; ') + ' (points of the result).'
}

/** The calibrated run as it stands, said. */
export function calibratedRunSaid(check: LiveCheck): string {
  const last = check.calibrated
  if (!check.link) return 'No backtest is linked: link one to have it calibrated each evening.'
  if (!last) return 'Not made yet: it is asked each evening once the market is closed, after the nightly runs.'
  if (last.error && !last.run) return last.error
  if (last.state === 'queued' || last.state === 'running') return 'Being made.'
  if (last.state === 'done') return 'Made after the last finished day.'
  return last.error ?? 'It did not finish: it is asked again with the next template.'
}

// ── a day ──

export const FATE_LABEL: Record<string, string> = {
  executed: 'Traded',
  refused: 'Refused',
  failed: 'Failed',
  closed: 'Closed',
  ignored: 'Ignored',
  superseded: 'Replaced',
  unpriced: 'No turbo recorded',
  not_a_signal: 'Not a signal',
  not_sent: 'Not sent',
  not_journaled: 'Not received',
  shadow: 'Shadow',
  unknown: 'Not known',
}

/** What became of a signal on a track: its fate and why, else nothing there. */
export function fateSaid(side: LiveCheckSignalSide | null | undefined): string {
  if (!side) return '—'
  const fate = side.fate ? (FATE_LABEL[side.fate] ?? side.fate) : '—'
  return side.reason ? `${fate}: ${side.reason}` : fate
}

/** The engine's and the trader's times of a live signal, from its bar's close. */
export function liveTimesSaid(side: LiveCheckSignalSide | null | undefined): string | null {
  if (!side?.engine) return null
  const parts = []
  if (side.engine.raised_seconds != null) parts.push(`raised ${secondsText(side.engine.raised_seconds)}`)
  if (side.engine.sent_seconds != null) parts.push(`sent ${secondsText(side.engine.sent_seconds)}`)
  if (side.trader?.decided_seconds != null) parts.push(`decided ${secondsText(side.trader.decided_seconds)}`)
  return parts.length > 0 ? `${parts.join(', ')} after the bar’s close` : null
}

/** A signal's tracks: whether each has it. */
export function signalOn(signal: LiveCheckSignal, track: LiveCheckTrack): LiveCheckSignalSide | null {
  return signal[track] ?? null
}

/** A trade of a track in short: its turbo's price in and out and its result. */
export function tradeSaid(trade: LiveCheckTrade | null | undefined): string {
  if (!trade) return '—'
  const result = trade.result == null ? '' : ` → ${pointsText(trade.result).replace(' pt', ' %')}`
  const prices = trade.entry_price != null && trade.exit_price != null ? `${trade.entry_price} → ${trade.exit_price}` : 'not priced'
  return `${prices}${result}`
}

/** How a trade of a track differs from paper's: seconds and percent apart at its entry and exit. */
export function apartSaid(trade: LiveCheckTrade | null | undefined, paper: LiveCheckTrade | null | undefined): string | null {
  if (!trade || !paper || trade === paper) return null
  const parts = []
  const at = (value: string | null) => (value ? Date.parse(value) : NaN)
  const entrySeconds = (at(trade.filled_at) - at(paper.filled_at)) / 1000
  if (Number.isFinite(entrySeconds)) {
    parts.push(`bought ${secondsText(Math.abs(entrySeconds))} ${entrySeconds >= 0 ? 'after' : 'before'} paper`)
  }
  if (trade.entry_price && paper.entry_price) {
    parts.push(`at ${pointsText(100 * (trade.entry_price / paper.entry_price - 1)).replace(' pt', ' %')} of its price`)
  }
  if (trade.exit_group && paper.exit_group && trade.exit_group !== paper.exit_group) parts.push(`exit: ${trade.exit_group}`)
  return parts.length > 0 ? parts.join(', ') : null
}

/** The trades of a day whose live and paper turbos differ. */
export function otherTurbos(rows: LiveCheckTradeRow[]): number {
  return rows.filter((row) => row.same_turbo === false).length
}

// ── a day drawn ──

const LETTER: Record<LiveCheckTrack, string> = { backtest: 'B', calibrated: 'C', paper: 'P', live: 'L' }

function moment(iso: string | null | undefined): number | null {
  if (!iso) return null
  const value = Date.parse(iso) / 1000
  return Number.isFinite(value) ? value : null
}

/**
 * The day's trades of each track drawn over the rule's bars: an arrow where each was bought (B, C, P, L: backtest,
 * calibrated, paper, live) and a dot where it was sold, coloured by its result.
 */
export function liveCheckLayer(rows: readonly LiveCheckTradeRow[], present: readonly LiveCheckTrack[]): LayerOf {
  return (bars, colors, dayStart) => {
    const locate = barLocator(bars)
    const labels: DrawingLabel[] = []
    for (const row of rows) {
      for (const track of TRACKS) {
        const trade = present.includes(track) ? row[track] : null
        if (!trade) continue
        const long = (trade.direction ?? row.action) !== 'short'
        const bought = moment(trade.filled_at)
        const boughtAt = bought === null ? null : locate(bought)
        if (boughtAt !== null) {
          labels.push({
            id: `${track}-in-${row.key}`, paneId: 'unrouted', xloc: 'bar_time', x: boughtAt * 1000, y: 0,
            yloc: long ? 'belowbar' : 'abovebar', text: LETTER[track], style: long ? 'arrowup' : 'arrowdown',
            color: colors.entry, textColor: colors.entry, size: 'small', textAlign: 'center', fontFamily: 'default',
            tooltip: `${TRACK_LABEL[track]}: bought ${trade.turbo.description ?? 'a turbo'} at ${trade.entry_price ?? '—'}`,
          })
        }
        const sold = moment(trade.exit_at)
        const soldAt = sold === null ? null : locate(sold)
        if (soldAt !== null) {
          const tone = trade.result == null ? 'neutral' : trade.result >= 0 ? 'profit' : 'loss'
          labels.push({
            id: `${track}-out-${row.key}`, paneId: 'unrouted', xloc: 'bar_time', x: soldAt * 1000, y: 0,
            yloc: long ? 'abovebar' : 'belowbar', text: LETTER[track], style: 'circle',
            color: colors[tone], textColor: colors[tone], size: 'small', textAlign: 'center', fontFamily: 'default',
            tooltip: `${TRACK_LABEL[track]}: sold at ${trade.exit_price ?? '—'}, ${trade.exit_group}, ${tradeSaid(trade)}`,
          })
        }
      }
    }
    labels.sort((first, second) => first.x - second.x)
    const { backgrounds } = runLayer([], [], bars, colors, dayStart)
    return { title: 'Each track’s trades', short: 'Trades', labels, trades: [], backgrounds }
  }
}
