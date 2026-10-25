// What a signal rule's dialog says of its days of bars before each day: how the TA
// engine uses them, and how many bars they are for the rule's asset, side and bars,
// as the server counts them with the engine's own code (src/admin/ta_history.py).
// Pure, so that it is tested alone.

import { formatNumber } from '@/lib/format'
import type { TaHistory } from '@/lib/types'

import { timeframeOf } from '../engine/timeframes'
import { MAX_HISTORY_DAYS, MAX_RANGE_TICKS } from './ruleModel'
import type { RuleDraft } from './ruleModel'

/** What the count of a draft's bars asks the server: its days only when it has its own. */
export interface HistoryRequest {
  epic: string
  side: 'bid' | 'ask'
  timeframe?: string
  ticks?: number
  days?: number
}

/** The request for a draft's count; null while its asset, bars or days are not yet a rule's. */
export function historyRequest(
  draft: Pick<RuleDraft, 'epic' | 'side' | 'barsType' | 'timeframe' | 'ticks' | 'historyDays'>,
): HistoryRequest | null {
  if (!draft.epic) return null
  const typed = draft.historyDays.trim()
  const days = typed === '' ? undefined : Number(typed)
  if (days !== undefined && (!Number.isInteger(days) || days < 0 || days > MAX_HISTORY_DAYS)) return null
  const request: HistoryRequest = { epic: draft.epic, side: draft.side, ...(days === undefined ? {} : { days }) }
  if (draft.barsType === 'range') {
    const ticks = Number(draft.ticks)
    return Number.isInteger(ticks) && ticks >= 1 && ticks <= MAX_RANGE_TICKS ? { ...request, ticks } : null
  }
  const timeframe = timeframeOf(draft.timeframe)
  return timeframe ? { ...request, timeframe: timeframe.pine } : null
}

function daysText(days: number): string {
  return `${days} day${days === 1 ? '' : 's'}`
}

function barsText(bars: number): string {
  return bars === 0 ? 'no bar' : `${formatNumber(bars)} bar${bars === 1 ? '' : 's'}`
}

/** The last restart_time ("21:15", UTC) at or before ``now``. */
export function lastRestart(restartTime: string, now: Date = new Date()): Date {
  const [hours, minutes] = restartTime.split(':').map(Number)
  const restart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hours, minutes))
  if (restart > now) restart.setUTCDate(restart.getUTCDate() - 1)
  return restart
}

/** A moment's time of day in the trading timezone: "22:15". */
function clockIn(moment: Date, timeZone: string): string {
  return new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }).format(moment)
}

/** A restart as the counts name it: "Sat 24 Oct, 22:15". */
function restartIn(moment: Date, timeZone: string): string {
  const day = new Intl.DateTimeFormat('en-GB', { timeZone, weekday: 'short', day: 'numeric', month: 'short' }).format(moment)
  return `${day.replace(',', '')}, ${clockIn(moment, timeZone)}`
}

/**
 * How the engine uses a rule's days of history, under the field: when it starts the rule again (its
 * restart_time, read in the trading timezone at the next restart and in UTC), on what, and what more days cost.
 */
export function historyHelp(restartTime: string, engineDays: number, timeZone: string, now: Date = new Date()): string[] {
  const next = lastRestart(restartTime, now)
  next.setUTCDate(next.getUTCDate() + 1)
  const at = clockIn(next, timeZone)
  return [
    `Each day at ${at} (${restartTime} UTC) the engine starts the rule again on the bars of these days before: it runs ` +
      'the script over them once, without sending any signal, then adds each new bar. The script runs on these days of ' +
      'bars just after the restart, and on one day more by the next one.',
    'A day is 24 hours counted back from the restart: a weekend or a holiday counts, and brings no bar. More days give ' +
      'its indicators a longer warm-up, and cost more: the engine runs the script over all of its bars at the restart and ' +
      'every ten minutes to check its run (at each new bar when the script cannot continue its run), and holds them in ' +
      `memory. Left empty: the engine’s history_days, ${daysText(engineDays)}.`,
  ]
}

/** What the count says of the rule's bars: those it was started on at the last restart, and those it would run on now. */
export function historySentence(history: TaHistory, timeZone: string): string {
  const restart = restartIn(new Date(history.restarted_at), timeZone)
  const now = barsText(history.now)
  if (history.history_days === 0) {
    return `With no day before, the script starts at each restart on no bar, and would run on ${now} now, made since the last one (${restart}).`
  }
  if (history.at_restart === 0) {
    const before = history.history_days === 1 ? 'the day before it had' : `the ${daysText(history.history_days)} before it had`
    return `The script was started on no bar at the last restart (${restart}): ${before} none. It would run on ${now} now.`
  }
  return `The script was started on ${barsText(history.at_restart)} at the last restart (${restart}), and would run on ${now} now.`
}

/** Each engine day of the week counted, named by the day it ends on in the trading timezone: "Mon 19". */
export function engineDays(history: TaHistory, timeZone: string): { label: string; bars: string; end: string }[] {
  const format = new Intl.DateTimeFormat('en-GB', { timeZone, weekday: 'short', day: 'numeric' })
  return history.engine_days.map((day) => ({
    label: format.format(new Date(day.end)).replace(',', ''),
    bars: formatNumber(day.bars),
    end: day.end,
  }))
}
