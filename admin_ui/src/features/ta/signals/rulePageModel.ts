// A signal rule's own page (SignalRulePage.tsx), what can be wrong there without a browser: the day it opens on and
// the days it may step to, its journal's signals drawn over a day (the day chart's second layer), its history in
// words, and what is said of its readiness and of a live rule's signals. Pure.

import type { DrawingLabel } from '@luxalgo/vela'
import { formatDateTime } from 'ioiti-ui'
import type { Check, TimelineItem, TimelineStatus } from 'ioiti-ui'

import type { MarkerTone } from '@/features/prices/chartData'
import type { TaRule, TaRuleBars, TaRuleChange, TaRuleEvent, TaRuleRanScript, TaSignal } from '@/lib/types'

import { barLocator, runLayer } from '../chart/runDayModel'
import type { LayerOf } from '../chart/runDayModel'
import { dayIn, nextWeekday } from '../data/wataBars'
import { holdingBack } from './readiness'
import { barsLabel } from './ruleModel'

const isWeekend = (day: string) => [0, 6].includes(new Date(`${day}T00:00:00Z`).getUTCDay())

function instant(iso: string | null | undefined): number | null {
  if (!iso) return null
  const value = Date.parse(iso) / 1000
  return Number.isFinite(value) ? value : null
}

// ── its days ──

/** The first day the rule could run: the day it was made, or the weekday after a weekend. */
export function firstDay(rule: Pick<TaRule, 'created_at'>, timeZone: string): string {
  const made = dayIn(Date.parse(rule.created_at), timeZone)
  return isWeekend(made) ? nextWeekday(made, 1) : made
}

/**
 * The day the page draws first: today on a weekday, else the weekday before; none before the rule's first day
 * (made on a Saturday, it has none until Monday).
 */
export function openingDay(rule: Pick<TaRule, 'created_at'>, now: number, timeZone: string): string | null {
  const today = dayIn(now, timeZone)
  const day = isWeekend(today) ? nextWeekday(today, -1) : today
  return day < firstDay(rule, timeZone) ? null : day
}

/** The weekday before and after `day` the page may step to: none before its first day, none after its opening one. */
export function steps(day: string, first: string, last: string): { before: string | null; after: string | null } {
  const before = nextWeekday(day, -1)
  const after = nextWeekday(day, 1)
  return { before: before < first ? null : before, after: after > last ? null : after }
}

// ── its signals over a day ──

const ACTION_WORD: Record<string, string> = { long: 'Long', short: 'Short', 'close-position': 'Close' }

/** What became of a journaled signal, in words, and the tone of its mark; `missed`: one the trader did not take. */
export function signalFate(signal: TaSignal): { said: string; tone: MarkerTone; missed: boolean } {
  const why = signal.detail ? `: ${signal.detail}` : ''
  switch (signal.outcome) {
    case 'shadow':
      return { said: 'journaled in shadow, never sent', tone: 'neutral', missed: false }
    case 'sending':
      return { said: 'being sent', tone: 'neutral', missed: false }
    case 'refused':
      return { said: `not sent${why}`, tone: 'warning', missed: true }
    case 'failed':
      return { said: `failed${why}`, tone: 'loss', missed: true }
  }
  const trader = signal.trader
  if (!trader) return { said: 'sent, the trader not decided yet', tone: 'neutral', missed: false }
  const reason = trader.reason ? `: ${trader.reason}` : ''
  if (trader.outcome === 'executed') return { said: 'sent, and bought by the trader', tone: 'entry', missed: false }
  if (trader.outcome === 'closed') return { said: 'sent, and its positions closed', tone: 'entry', missed: false }
  if (trader.outcome === 'ignored') return { said: 'sent, nothing to do', tone: 'neutral', missed: false }
  return { said: `sent, ${trader.outcome} by the trader${reason}`, tone: trader.outcome === 'failed' ? 'loss' : 'warning', missed: true }
}

/**
 * A day's signals of the rule as the day chart's second layer: each at the bar it was raised on, long under the
 * bars and the rest above, in the tone of what became of it (✕ for one the trader did not take), and the days of
 * history before the day shaded as a run's day is. No trade: a rule's page draws its signals.
 */
export function ruleLayer(signals: readonly TaSignal[]): LayerOf {
  return (rows, colors, dayStart) => {
    const locate = barLocator(rows)
    const labels: DrawingLabel[] = []
    for (const signal of signals) {
      const moment = instant(signal.bar_time) ?? instant(signal.bar_closed_at)
      const time = moment === null ? null : locate(moment)
      if (time === null) continue
      const action = signal.action ?? ''
      const fate = signalFate(signal)
      const word = ACTION_WORD[action] ?? 'Alert'
      labels.push({
        id: `signal-${signal.id}`,
        paneId: 'unrouted',
        xloc: 'bar_time',
        x: time * 1000,
        y: 0,
        yloc: action === 'long' ? 'belowbar' : 'abovebar',
        text: `${word}${fate.missed ? ' ✕' : ''}`,
        style: action === 'long' ? 'arrowup' : action === 'short' ? 'arrowdown' : 'circle',
        color: colors[fate.tone],
        textColor: colors[fate.tone],
        size: 'small',
        textAlign: 'center',
        fontFamily: 'default',
        tooltip: `#${signal.id} ${word}${signal.alert_title ? ` (${signal.alert_title})` : ''}, ${fate.said}`,
      })
    }
    labels.sort((first, second) => first.x - second.x)
    const { backgrounds } = runLayer([], [], rows, colors, dayStart)
    return { title: 'The rule: its signals', short: 'The rule', labels, trades: [], backgrounds }
  }
}

/** A live rule's signals of the last days, in a sentence: sent, bought, closing, not taken. */
export function liveSignalsSaid(signals: readonly TaSignal[], days: number): string {
  if (signals.length === 0) return `No signal in the last ${days} days.`
  const sent = signals.filter((signal) => signal.outcome === 'sent')
  const bought = sent.filter((signal) => signal.trader?.outcome === 'executed').length
  const closing = sent.filter((signal) => signal.trader?.outcome === 'closed').length
  const missed = signals.filter((signal) => signalFate(signal).missed).length
  const counted = (count: number, what: string) => `${count} ${what}${count === 1 ? '' : 's'}`
  return `In the last ${days} days: ${counted(sent.length, 'signal')} sent, ${bought} bought by the trader, ${closing} closing positions, ${missed} not taken.`
}

// ── going live ──

/** Its readiness in a few words: what the server would hold it back for (`holdingBack`). */
export function readinessSaid(checks: Check[]): { text: string; ready: boolean } {
  const missing = holdingBack(checks).length
  return missing === 0 ? { text: 'Ready to go live', ready: true } : { text: `${missing} to go before live`, ready: false }
}

// ── its history ──

const FIELD_WORD: Record<string, string> = {
  name: 'name',
  alerts: 'alerts',
  alert_calls: 'alert() calls',
  confidence_plot: 'confidence plot',
  indice: 'indice',
  asset: 'asset',
  side: 'side',
  bars: 'bars',
  script: 'script',
  libraries: 'libraries',
  inputs: 'inputs',
  history_days: 'days of history',
}

/** Words said one after the other: "name", "name and alerts", "name, alerts and indice". */
function listSaid(words: string[]): string {
  return words.length < 2 ? (words[0] ?? '') : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

const fieldsSaid = (fields: string[]) => listSaid(fields.map((field) => FIELD_WORD[field] ?? field))

function scriptSaid(script: TaRuleRanScript | null): string {
  if (!script) return 'none'
  return `${script.name ?? 'a script since deleted'} v${script.version ?? '?'}`
}

function librariesSaid(libraries: { name: string; version: number }[]): string {
  return libraries.length === 0 ? 'none' : libraries.map((entry) => `${entry.name} v${entry.version}`).join(', ')
}

function assetSaid(asset: { provider: string; key: string } | null): string {
  return asset ? `${asset.provider}/${asset.key}` : 'none'
}

function barsSaid(bars: TaRuleBars | null): string {
  return bars ? barsLabel(bars) : 'none'
}

function daysSaid(days: number | null): string {
  if (days === null) return 'the engine’s'
  return days === 0 ? 'none' : `${days} day${days === 1 ? '' : 's'}`
}

function valueSaid(value: number | string | boolean | null): string {
  return value === null ? 'none' : String(value)
}

/** One field of what it runs that a change changed, as a sentence. */
export function changeSaid(change: TaRuleChange): string {
  switch (change.what) {
    case 'script':
      return `Its script: ${scriptSaid(change.before)} → ${scriptSaid(change.after)}.`
    case 'libraries':
      return `Its libraries: ${librariesSaid(change.before)} → ${librariesSaid(change.after)}.`
    case 'asset':
      return `Its asset: ${assetSaid(change.before)} → ${assetSaid(change.after)}.`
    case 'side':
      return `Its side: ${change.before ?? 'none'} → ${change.after ?? 'none'}.`
    case 'bars':
      return `Its bars: ${barsSaid(change.before)} → ${barsSaid(change.after)}.`
    case 'history_days':
      return `Its days of history: ${daysSaid(change.before)} → ${daysSaid(change.after)}.`
    case 'input':
      if (change.after === null) return `${change.name}: ${valueSaid(change.before)}, no longer set.`
      if (change.before === null) return `${change.name}: set to ${valueSaid(change.after)}.`
      return `${change.name}: ${valueSaid(change.before)} → ${valueSaid(change.after)}.`
  }
}

const proposedSaid = (proposed: string | null) => (proposed ? [`Proposed by the AI agent: “${proposed}”.`] : [])

/** An event of its history in words: a title, what it changed, and how the timeline marks it. */
export function eventSaid(event: TaRuleEvent): { title: string; details: string[]; status: TimelineStatus } {
  switch (event.kind) {
    case 'made': {
      const runs = event.runs
      return {
        title: event.by === 'claude' ? 'Made by Claude' : event.by ? `Made by ${event.by}` : 'Made',
        details: runs
          ? [
              `${scriptSaid(runs.script)} on ${[assetSaid(runs.asset), runs.side].filter(Boolean).join(' ')}, ${barsSaid(runs.bars)}`
                + (runs.inputs > 0 ? `, ${runs.inputs} input${runs.inputs === 1 ? '' : 's'} set` : '')
                + '.',
            ]
          : ['What it ran then was not kept: it was made before WATA kept what each rule runs.'],
        status: 'completed',
      }
    }
    case 'changed':
      return {
        title: event.live ? 'Changed while live' : 'Changed',
        details: [...event.changes.map(changeSaid), ...(event.also.length > 0 ? [`Its ${fieldsSaid(event.also)} too.`] : [])],
        status: event.live ? 'warning' : 'completed',
      }
    case 'live':
      return {
        title: event.unmet.length > 0 ? 'Went live without its readiness' : 'Went live',
        details: [
          ...(event.unmet.length > 0 ? [`Still missing: ${event.unmet.join('; ')}.`] : []),
          ...(event.licence ? [`The licence of its script (${event.licence}) checked.`] : []),
          ...proposedSaid(event.proposed),
        ],
        status: event.unmet.length > 0 ? 'warning' : 'completed',
      }
    case 'left_live':
      return {
        title: event.to === 'off' ? 'Out of live, switched off' : `Out of live, to ${event.to}`,
        details: proposedSaid(event.proposed),
        status: 'completed',
      }
    case 'changed_live':
      return { title: 'Changed while live', details: [`Its ${fieldsSaid(event.fields)}: nothing it runs.`], status: 'warning' }
  }
}

/** Its history, newest first as the server gives it, as the timeline's items. */
export function historyItems(events: readonly TaRuleEvent[], timeZone: string): TimelineItem[] {
  return events.map((event, index) => {
    const said = eventSaid(event)
    return {
      id: `${event.kind}-${index}`,
      title: said.title,
      timestamp: formatDateTime(event.at, timeZone),
      status: said.status,
      ...(said.details.length > 0 ? { description: said.details.join(' ') } : {}),
    }
  })
}
