// A signal rule between the page and the admin: the draft its dialog edits, what
// is saved of it, and what a save changes. Pure, so that it is tested alone.

import type { IPineInput } from 'pinets'

import type { ChartMarker } from '@/features/prices/chartData'
import type { TaAlertAction, TaComparisonRow, TaRule, TaRuleBars, TaRuleFields, TaRuleMode, TaScriptSummary } from '@/lib/types'

import { timeframeOf } from '../engine/timeframes'

export const MODE_LABEL: Record<TaRuleMode, string> = { off: 'Off', shadow: 'Shadow', live: 'Live' }

export const MODE_TONE: Record<TaRuleMode, 'neutral' | 'warning' | 'danger'> = {
  off: 'neutral',
  shadow: 'warning',
  live: 'danger',
}

export const ALERT_ACTIONS: { value: TaAlertAction; label: string }[] = [
  { value: 'ignore', label: 'Nothing' },
  { value: 'long', label: 'Long' },
  { value: 'short', label: 'Short' },
  { value: 'close-position', label: 'Close positions' },
  { value: 'message', label: 'What its message says' },
]

export const ACTION_LABEL: Record<TaAlertAction, string> = Object.fromEntries(
  ALERT_ACTIONS.map((entry) => [entry.value, entry.label]),
) as Record<TaAlertAction, string>

export const MAX_RANGE_TICKS = 100_000
const INDICE = /^[a-z0-9][a-z0-9_-]{0,31}$/

export function barsLabel(bars: TaRuleBars): string {
  if (bars.type === 'range') return `Range ${bars.ticks}`
  return timeframeOf(bars.timeframe)?.label ?? bars.timeframe
}

/** The newer versions a rule could run: of its script, and of each library it pinned. */
export function updatesOf(rule: TaRule): { script: number | null; libraries: { name: string; from: number; to: number }[] } {
  const script = rule.latest.script !== null && rule.latest.script > rule.script.version ? rule.latest.script : null
  const libraries = rule.libraries.flatMap((entry) => {
    const latest = rule.latest.libraries[entry.name]
    return latest != null && latest > entry.version ? [{ name: entry.name, from: entry.version, to: latest }] : []
  })
  return { script, libraries }
}

/** The alerts that make signals, as a sentence: what going live sends. */
export function mappedAlerts(rule: Pick<TaRule, 'alerts' | 'alert_calls'>): string[] {
  const mapped = rule.alerts
    .filter((entry) => entry.action !== 'ignore')
    .map((entry) => `“${entry.title}” → ${ACTION_LABEL[entry.action].toLowerCase()}`)
  if (rule.alert_calls === 'message') mapped.push('every alert() call → what its message says')
  return mapped
}

export type InputValue = number | string | boolean

export interface RuleDraft {
  name: string
  scriptId: string
  version: number
  epic: string
  side: 'bid' | 'ask'
  barsType: 'time' | 'range'
  timeframe: string
  ticks: string
  indice: string
  /** The inputs the rule sets, by their id in the script; the others keep the script's defaults. */
  inputs: Record<string, InputValue>
  /** What the rule knew of its inputs when it was opened: kept while the script's own are not read. */
  inputNames: Record<string, { var: string | null; title: string | null }>
  /** What each alertcondition() becomes, by its title. */
  alerts: Record<string, TaAlertAction>
  alertCalls: 'ignore' | 'message'
  confidencePlot: string
}

export function draftOfRule(rule: TaRule): RuleDraft {
  return {
    name: rule.name,
    scriptId: rule.script.id,
    version: rule.script.version,
    epic: rule.asset.key,
    side: rule.side,
    barsType: rule.bars.type,
    timeframe: rule.bars.type === 'time' ? rule.bars.timeframe : '1',
    ticks: rule.bars.type === 'range' ? String(rule.bars.ticks) : '60',
    indice: rule.indice,
    inputs: Object.fromEntries(rule.inputs.map((entry) => [entry.id, entry.value])),
    inputNames: Object.fromEntries(rule.inputs.map((entry) => [entry.id, { var: entry.var, title: entry.title }])),
    alerts: Object.fromEntries(rule.alerts.map((entry) => [entry.title, entry.action])),
    alertCalls: rule.alert_calls,
    confidencePlot: rule.confidence_plot ?? '',
  }
}

export function newDraft(script: TaScriptSummary | undefined, instrument: { epic: string; label: string } | undefined, indices: string[]): RuleDraft {
  const indice = instrument && indices.includes(instrument.label) ? instrument.label : indices[0] ?? instrument?.label ?? ''
  return {
    name: script && instrument ? `${script.name} on ${instrument.epic}` : '',
    scriptId: script?.id ?? '',
    version: script?.version ?? 1,
    epic: instrument?.epic ?? '',
    side: 'bid',
    barsType: 'time',
    timeframe: '1',
    ticks: '60',
    indice,
    inputs: {},
    inputNames: {},
    alerts: {},
    alertCalls: 'ignore',
    confidencePlot: '',
  }
}

/** A value typed for an input, as its type has it; null when it is not one. */
export function inputValueOf(meta: Pick<IPineInput, 'type' | 'minval' | 'maxval'>, raw: string | boolean): InputValue | null {
  if (typeof raw === 'boolean') return meta.type === 'bool' ? raw : null
  if (meta.type === 'int' || meta.type === 'float' || meta.type === 'price' || meta.type === 'time') {
    if (raw.trim() === '') return null
    const value = Number(raw)
    if (!Number.isFinite(value) || (meta.type === 'int' && !Number.isInteger(value))) return null
    if ((meta.minval !== undefined && value < meta.minval) || (meta.maxval !== undefined && value > meta.maxval)) return null
    return value
  }
  return raw
}

function sameValue(first: unknown, second: unknown): boolean {
  return first === second || (typeof first === 'number' && typeof second === 'number' && Math.abs(first - second) < 1e-12)
}

/**
 * What is saved of a draft. An input is saved when it differs from the script's
 * default (``meta``, the script's inputs as PineTS reads them); without them,
 * the draft's inputs are kept as they are.
 */
export function ruleFields(draft: RuleDraft, meta: IPineInput[] | null): TaRuleFields {
  const byId = new Map((meta ?? []).map((input) => [input.id, input]))
  const inputs = Object.entries(draft.inputs)
    .filter(([id, value]) => {
      if (meta === null) return true
      const input = byId.get(id)
      return input !== undefined && !sameValue(input.defval, value)
    })
    .map(([id, value]) => {
      const input = byId.get(id)
      const known = draft.inputNames[id]
      return { id, var: input ? input.varId ?? null : known?.var ?? null, title: input ? input.title ?? null : known?.title ?? null, value }
    })
    .sort((first, second) => Number(first.id.slice(3)) - Number(second.id.slice(3)))
  const bars: TaRuleBars =
    draft.barsType === 'range' ? { type: 'range', ticks: Number(draft.ticks) } : { type: 'time', timeframe: draft.timeframe }
  return {
    name: draft.name.trim(),
    asset: { provider: 'capital', key: draft.epic },
    side: draft.side,
    bars,
    script: { id: draft.scriptId, version: draft.version },
    inputs,
    alerts: Object.entries(draft.alerts)
      .filter(([, action]) => action !== 'ignore')
      .map(([title, action]) => ({ title, action })),
    alert_calls: draft.alertCalls,
    confidence_plot: draft.confidencePlot || null,
    indice: draft.indice,
  }
}

/** What stops a draft from being saved, in the words of its fields. */
export function draftProblems(draft: RuleDraft, otherNames: string[]): string[] {
  const problems: string[] = []
  const name = draft.name.trim()
  if (!name) problems.push('The rule needs a name.')
  else if (otherNames.some((other) => other.toLocaleLowerCase() === name.toLocaleLowerCase())) {
    problems.push(`A rule named “${name}” already exists.`)
  }
  if (!draft.scriptId) problems.push('Choose the script it runs.')
  if (!draft.epic) problems.push('Choose the asset it runs on.')
  if (draft.barsType === 'range') {
    const ticks = Number(draft.ticks)
    if (!Number.isInteger(ticks) || ticks < 1 || ticks > MAX_RANGE_TICKS) problems.push(`Range bars are 1 to ${MAX_RANGE_TICKS} ticks.`)
  }
  if (!INDICE.test(draft.indice)) problems.push('Choose the indice its signals are for.')
  return problems
}

function described(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export interface RuleChange {
  label: string
  before: string
  after: string
}

/** What saving ``after`` changes of ``before`` (everything, for a new rule), as the review lists it. */
export function ruleChanges(before: TaRule | null, after: TaRuleFields, scriptName: (id: string) => string): RuleChange[] {
  const changes: RuleChange[] = []
  const add = (label: string, old: unknown, next: unknown) => {
    if (before !== null && described(old) === described(next)) return
    changes.push({ label, before: before === null ? '—' : described(old), after: described(next) })
  }
  add('Name', before?.name, after.name)
  add(
    'Script',
    before ? `${scriptName(before.script.id)} v${before.script.version}` : null,
    `${scriptName(after.script.id)} v${after.script.version}`,
  )
  add('Asset', before ? `${before.asset.key} · ${before.side}` : null, `${after.asset.key} · ${after.side}`)
  add('Bars', before ? barsLabel(before.bars) : null, barsLabel(after.bars))
  add('Indice', before?.indice, after.indice)
  const inputsOf = (entries: { id: string; title: string | null; value: InputValue }[]) =>
    entries.map((entry) => `${entry.title ?? entry.id} = ${described(entry.value)}`).join(', ') || 'the script’s defaults'
  add('Inputs', before ? inputsOf(before.inputs) : null, inputsOf(after.inputs))
  const alertsOf = (rule: Pick<TaRule, 'alerts' | 'alert_calls'>) => mappedAlerts(rule).join('; ') || 'none'
  add('Signals', before ? alertsOf(before) : null, alertsOf(after))
  add('Confidence', before?.confidence_plot ?? null, after.confidence_plot)
  return changes
}

const SHAPE: Record<string, ChartMarker['shape']> = { long: 'arrowUp', short: 'arrowDown' }

/** Each side's signals on the bars of the day: WATA's above them, TradingView's below, the ones left alone to be seen first. */
export function comparisonMarkers(rows: TaComparisonRow[], locate: (instant: number) => number | null): ChartMarker[] {
  const markers: ChartMarker[] = []
  rows.forEach((row, index) => {
    const paired = row.wata !== null && row.tradingview !== null
    const sides = [
      { signal: row.wata, at: row.wata?.bar_time, position: 'aboveBar' as const, who: 'WATA' },
      { signal: row.tradingview, at: row.tradingview?.signal_timestamp, position: 'belowBar' as const, who: 'TV' },
    ]
    for (const side of sides) {
      if (!side.signal || !side.at) continue
      const time = locate(Date.parse(side.at) / 1000)
      if (time === null) continue
      markers.push({
        id: `${index}-${side.who}`,
        time,
        shape: SHAPE[row.action ?? ''] ?? 'circle',
        tone: paired ? (side.who === 'WATA' ? 'entry' : 'neutral') : 'warning',
        text: `${side.who} ${row.action ?? ''}`.trim(),
        position: side.position,
      })
    }
  })
  return markers.sort((first, second) => first.time - second.time)
}
