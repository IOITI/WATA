import type { IPineInput } from 'pinets'

import { timeframeOf } from '@/features/ta/engine/timeframes'
import { draftOfRule, inputValueOf, MAX_RANGE_TICKS, newDraft, ruleFields } from '@/features/ta/signals/ruleModel'
import type { RuleDraft } from '@/features/ta/signals/ruleModel'
import type {
  BacktestAsset,
  BacktestField,
  BacktestOptions,
  BacktestRequestBody,
  BacktestRun,
  BacktestSweepAxis,
  BacktestTurbos,
  BacktestWindows,
  BacktestValue,
  DataAsset,
  TaRule,
  TaRuleBars,
  TaScriptSummary,
} from '@/lib/types'

import { PROVIDER_LABEL } from './model'

/**
 * The form of a new backtest, as plain data: what it holds while it is
 * filled in, what stops it from being sent, and the request it becomes.
 * Tested without a page.
 */

export type SourceKind = 'pine' | 'journal'

export interface AxisDraft {
  kind: 'setting' | 'input'
  name: string
  /** The values as typed: "8, 12, 15". */
  values: string
}

export interface BacktestDraft {
  name: string
  tags: string
  from: string
  to: string
  indice: string
  sourceKind: SourceKind
  /** The asset the script runs on, or the replay's days are drawn on: "capital/US100". */
  asset: string
  /** The script, its inputs and what its alerts become, as a signal rule holds them; its price side and bars. */
  rule: RuleDraft
  fromRule: { id: string; name: string } | null
  /** What the run changes of the live settings, as typed. */
  changes: Record<string, string>
  repetitions: string
  seed: string
  sweep: boolean
  axes: AxisDraft[]
  heldOut: string
  /** "TRAIN/TEST" days of a walk-forward's windows, or nothing. */
  walkForward: string
  /** The turbos traded: those the price recorder kept, or made up from the index. */
  turbos: BacktestTurbos
}

/** The days a period may have: more on turbos made up from the index. */
export function periodLimit(draft: Pick<BacktestDraft, 'turbos'>, options: BacktestOptions): number {
  return draft.turbos === 'modelled' ? (options.limits.modelled_period_days ?? options.limits.period_days) : options.limits.period_days
}

export function parseAsset(text: string): BacktestAsset | null {
  const [provider, ...rest] = text.split('/')
  const key = rest.join('/')
  if (!key || !['capital', 'dukascopy', 'saxo'].includes(provider)) return null
  return { provider: provider as BacktestAsset['provider'], key }
}

export function assetRef(asset: BacktestAsset): string {
  return `${asset.provider}/${asset.key}`
}

/** The assets a strategy can run on: Capital.com's, Dukascopy's histories, and the indices the recorder kept. */
export function strategyAssets(assets: readonly DataAsset[]): { value: string; label: string }[] {
  return assets
    .filter((asset) => asset.provider !== 'saxo' || asset.role === 'underlying')
    .map((asset) => ({
      value: asset.ref,
      label: `${PROVIDER_LABEL[asset.provider as keyof typeof PROVIDER_LABEL] ?? asset.provider} · ${asset.description ?? asset.symbol ?? asset.key}`,
    }))
}

export function newBacktestDraft(
  options: BacktestOptions,
  script: TaScriptSummary | undefined,
  asset: string,
  period: { from: string; to: string },
): BacktestDraft {
  const rule = newDraft(script, undefined, options.indices)
  return {
    name: '',
    tags: '',
    from: period.from,
    to: period.to,
    indice: options.indices[0] ?? 'us100',
    sourceKind: 'pine',
    asset,
    rule: { ...rule, name: '' },
    fromRule: null,
    changes: {},
    repetitions: String(options.baseline.repetitions),
    seed: String(options.baseline.seed),
    sweep: false,
    axes: [],
    heldOut: String(options.sweep.held_out),
    walkForward: '',
    turbos: 'recorded',
  }
}

/** A draft filled from a signal rule: its script, asset, bars, inputs and alerts. */
export function withRule(draft: BacktestDraft, rule: TaRule): BacktestDraft {
  return {
    ...draft,
    sourceKind: 'pine',
    asset: assetRef(rule.asset),
    indice: rule.indice,
    rule: draftOfRule(rule),
    fromRule: { id: rule.id, name: rule.name },
  }
}

/** A draft of a run made before: the same, to be changed and asked for again. */
export function draftOfRun(run: BacktestRun, options: BacktestOptions): BacktestDraft {
  const request = run.request
  const base = newBacktestDraft(options, undefined, assetRef(request.chart.asset), { from: request.from, to: request.to })
  const changes = Object.fromEntries(Object.entries(request.changes).map(([name, value]) => [name, String(value)]))
  const draft: BacktestDraft = {
    ...base,
    name: `${request.name} (again)`.slice(0, options.limits.name_length),
    tags: request.tags.join(', '),
    indice: request.indice,
    changes,
    repetitions: String(request.baseline?.repetitions ?? options.baseline.repetitions),
    seed: String(request.baseline?.seed ?? options.baseline.seed),
    sweep: request.sweep !== null,
    axes: (request.sweep?.axes ?? []).map((axis) => ({ kind: axis.kind, name: axis.name, values: axis.values.map(String).join(', ') })),
    heldOut: String(request.sweep?.held_out ?? options.sweep.held_out),
    walkForward: request.sweep?.walk_forward ? `${request.sweep.walk_forward.train_days}/${request.sweep.walk_forward.test_days}` : '',
    // Made up again only while the turbos are measured.
    turbos: request.turbos === 'modelled' && options.turbo_model ? 'modelled' : 'recorded',
  }
  if (request.source.kind === 'journal') {
    return { ...draft, sourceKind: 'journal', rule: { ...draft.rule, ...barsDraft(request.chart.bars), side: request.chart.side } }
  }
  const rule = request.source.rule
  return {
    ...draft,
    sourceKind: 'pine',
    fromRule: request.source.from_rule,
    rule: {
      ...draft.rule,
      scriptId: rule.script.id,
      version: rule.script.version,
      side: rule.side,
      ...barsDraft(rule.bars),
      inputs: Object.fromEntries(rule.inputs.map((entry) => [entry.id, entry.value])),
      inputNames: Object.fromEntries(rule.inputs.map((entry) => [entry.id, { var: entry.var, title: entry.title }])),
      alerts: Object.fromEntries(rule.alerts.map((entry) => [entry.title, entry.action])),
      alertCalls: rule.alert_calls,
      confidencePlot: rule.confidence_plot ?? '',
    },
  }
}

function barsDraft(bars: TaRuleBars): Pick<RuleDraft, 'barsType' | 'timeframe' | 'ticks'> {
  return bars.type === 'range'
    ? { barsType: 'range', timeframe: '1', ticks: String(bars.ticks) }
    : { barsType: 'time', timeframe: bars.timeframe, ticks: '60' }
}

/** The bars asked for: a length typed (90s, 2m) as Pine names it (90S, 2). */
export function draftBars(draft: BacktestDraft): TaRuleBars {
  if (draft.rule.barsType === 'range') return { type: 'range', ticks: Number(draft.rule.ticks) }
  return { type: 'time', timeframe: timeframeOf(draft.rule.timeframe)?.pine ?? draft.rule.timeframe }
}

// ── a setting's value, as typed ──

export function fieldValue(field: Pick<BacktestField, 'kind' | 'choices'>, text: string): BacktestValue | null {
  const value = text.trim()
  if (field.kind === 'boolean') return value === 'true' ? true : value === 'false' ? false : null
  if (field.kind === 'time') return /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(value) ? value : null
  if (field.kind === 'choice') return field.choices.includes(value) ? value : null
  if (value === '') return null
  const number = Number(value)
  if (!Number.isFinite(number)) return null
  if (field.kind === 'integer' && !Number.isInteger(number)) return null
  return number
}

/** Why a value typed for a setting cannot be used; null when it can. */
export function fieldError(field: BacktestField, text: string): string | null {
  const value = fieldValue(field, text)
  if (value === null) {
    if (field.kind === 'time') return 'A time of day, HH:MM.'
    if (field.kind === 'integer') return 'A whole number.'
    if (field.kind === 'choice') return `One of ${field.choices.join(', ')}.`
    if (field.kind === 'boolean') return 'On or off.'
    return 'A number.'
  }
  if (typeof value === 'number') {
    if (field.least !== null && value < field.least) return `${field.least} at least.`
    if (field.most !== null && value > field.most) return `${field.most} at most.`
  }
  return null
}

/** The changes of a draft that differ from the live value, read as their settings take them. */
export function changesOf(draft: BacktestDraft, fields: readonly BacktestField[]): Record<string, BacktestValue> {
  const changes: Record<string, BacktestValue> = {}
  for (const [name, text] of Object.entries(draft.changes)) {
    const field = fields.find((entry) => entry.name === name)
    if (!field) continue
    const value = fieldValue(field, text)
    if (value !== null && value !== field.value) changes[name] = value
  }
  return changes
}

// ── a sweep's walk-forward ──

const WALK_FORWARD = [
  { value: '', label: 'none' },
  { value: '5/1', label: 'choose on 5 days, keep 1' },
  { value: '10/5', label: 'choose on 10 days, keep 5' },
  { value: '20/5', label: 'choose on 20 days, keep 5' },
  { value: '40/10', label: 'choose on 40 days, keep 10' },
]

/** The windows a walk-forward can be asked with, the one a run was made with among them. */
export function walkForwardChoices(current: string): { value: string; label: string }[] {
  if (WALK_FORWARD.some((choice) => choice.value === current)) return WALK_FORWARD
  const windows = windowsOf(current)
  return windows ? [...WALK_FORWARD, { value: current, label: `choose on ${windows.train_days} days, keep ${windows.test_days}` }] : WALK_FORWARD
}

/** "10/5" as the windows of a walk-forward; nothing for anything else. */
export function windowsOf(text: string): BacktestWindows | null {
  const found = /^(\d+)\/(\d+)$/.exec(text.trim())
  if (!found) return null
  const [train, test] = [Number(found[1]), Number(found[2])]
  return train >= 1 && test >= 1 ? { train_days: train, test_days: test } : null
}

// ── a sweep's axes ──

/** The values typed for an axis ("8, 12, 15"), or why they cannot be used. */
export function axisValues(
  axis: AxisDraft,
  fields: readonly BacktestField[],
  inputs: readonly IPineInput[] | null,
  most: number,
): { values: BacktestValue[] } | { error: string } {
  const parts = axis.values.split(',').map((part) => part.trim()).filter((part) => part !== '')
  if (parts.length < 2) return { error: 'Two values at least, separated by commas.' }
  if (parts.length > most) return { error: `${most} values at most.` }
  if (new Set(parts).size !== parts.length) return { error: 'Each value once.' }
  if (axis.kind === 'setting') {
    const field = fields.find((entry) => entry.name === axis.name)
    if (!field) return { error: 'Choose a setting.' }
    const values: BacktestValue[] = []
    for (const part of parts) {
      const text = field.kind === 'boolean' ? (['on', 'true', 'yes'].includes(part.toLowerCase()) ? 'true' : ['off', 'false', 'no'].includes(part.toLowerCase()) ? 'false' : part) : part
      const error = fieldError(field, text)
      if (error) return { error: `${part}: ${error}` }
      values.push(fieldValue(field, text) as BacktestValue)
    }
    return { values }
  }
  const input = inputs?.find((entry) => entry.id === axis.name)
  if (!input) return { error: 'Choose an input of the script.' }
  const values: BacktestValue[] = []
  for (const part of parts) {
    const value = inputValueOf(input, input.type === 'bool' ? part === 'true' : part)
    if (value === null) return { error: `${part}: not a value of this input.` }
    values.push(value)
  }
  return { values }
}

export function sweepCells(axes: readonly { values: unknown[] }[]): number {
  return axes.reduce((product, axis) => product * axis.values.length, axes.length ? 1 : 0)
}

// ── what stops it, and what it becomes ──

const DAY = /^\d{4}-\d{2}-\d{2}$/

export function periodDays(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1
}

/** What stops the draft from being sent, in the words of the form. */
export function backtestProblems(
  draft: BacktestDraft,
  options: BacktestOptions,
  inputs: readonly IPineInput[] | null,
): string[] {
  const problems: string[] = []
  if (!DAY.test(draft.from) || !DAY.test(draft.to)) problems.push('Choose the period.')
  else if (draft.to < draft.from) problems.push('The period ends before it starts.')
  else if (periodDays(draft.from, draft.to) > periodLimit(draft, options)) problems.push(`A period of ${periodLimit(draft, options)} days at most.`)
  if (draft.turbos === 'modelled' && !options.turbo_model) problems.push('The turbos have not been measured yet: measure them on the Backtests page.')
  if (!parseAsset(draft.asset)) problems.push('Choose the asset.')
  if (draft.rule.barsType === 'range') {
    const ticks = Number(draft.rule.ticks)
    if (!Number.isInteger(ticks) || ticks < 1 || ticks > MAX_RANGE_TICKS) problems.push(`Range bars are 1 to ${MAX_RANGE_TICKS} ticks.`)
  } else if (!timeframeOf(draft.rule.timeframe)) {
    problems.push('Bars of 10 seconds to a day: write their length as 45s, 90s or 2m.')
  }
  if (draft.sourceKind === 'pine') {
    if (!draft.rule.scriptId) problems.push('Choose the script.')
    const fields = ruleFields(draft.rule, inputs as IPineInput[] | null)
    if (fields.alerts.length === 0 && fields.alert_calls !== 'message') problems.push('Make at least one alert of the script a signal.')
  }
  for (const [name, text] of Object.entries(draft.changes)) {
    const field = options.fields.find((entry) => entry.name === name)
    const error = field ? fieldError(field, text) : null
    if (error) problems.push(`${field?.label}: ${error}`)
  }
  if (draft.sweep) {
    if (draft.axes.length === 0) problems.push('A sweep tries the values of one or two settings.')
    for (const axis of draft.axes) {
      const read = axisValues(axis, options.fields, inputs, options.sweep.values)
      if ('error' in read) problems.push(`${axis.name || 'An axis'}: ${read.error}`)
    }
    if (draft.axes.some((axis) => axis.kind === 'input') && draft.sourceKind === 'journal') {
      problems.push('A replay of the journal has no script inputs to try.')
    }
    const cells = sweepCells(draft.axes.map((axis) => {
      const read = axisValues(axis, options.fields, inputs, options.sweep.values)
      return { values: 'values' in read ? read.values : [] }
    }))
    if (cells > options.sweep.cells) problems.push(`${cells} combinations: ${options.sweep.cells} at most.`)
    const heldOut = Number(draft.heldOut)
    if (!(heldOut >= options.sweep.least && heldOut <= options.sweep.most)) {
      problems.push(`The days held out are ${options.sweep.least * 100} to ${options.sweep.most * 100} % of them.`)
    }
  } else {
    const repetitions = Number(draft.repetitions)
    if (!Number.isInteger(repetitions) || repetitions < 0 || repetitions > options.baseline.most) {
      problems.push(`0 to ${options.baseline.most} repetitions with entries at random.`)
    }
    const seed = Number(draft.seed)
    if (!Number.isInteger(seed) || seed < 0) problems.push('The seed is a whole number.')
  }
  return problems
}

export function tagsOf(text: string): string[] {
  return [...new Set(text.split(',').map((tag) => tag.trim()).filter(Boolean))]
}

/** The request a draft becomes. */
export function requestOf(draft: BacktestDraft, options: BacktestOptions, inputs: readonly IPineInput[] | null): BacktestRequestBody {
  const asset = parseAsset(draft.asset) as BacktestAsset
  const bars = draftBars(draft)
  const body: BacktestRequestBody = {
    name: draft.name.trim() || undefined,
    tags: tagsOf(draft.tags),
    from: draft.from,
    to: draft.to,
    indice: draft.indice,
    source: { kind: 'journal' },
    changes: changesOf(draft, options.fields),
    ...(draft.turbos === 'modelled' ? { turbos: 'modelled' as const } : {}),
  }
  if (draft.sourceKind === 'pine') {
    const fields = ruleFields(draft.rule, inputs as IPineInput[] | null)
    body.source = {
      kind: 'pine',
      script: fields.script,
      asset,
      side: draft.rule.side,
      bars,
      inputs: fields.inputs,
      alerts: fields.alerts,
      alert_calls: fields.alert_calls,
      confidence_plot: fields.confidence_plot,
      from_rule: draft.fromRule,
    }
  } else {
    body.chart = { asset, side: draft.rule.side, bars }
  }
  if (draft.sweep) {
    body.sweep = {
      axes: draft.axes.map((axis): BacktestSweepAxis => {
        const read = axisValues(axis, options.fields, inputs, options.sweep.values)
        return { kind: axis.kind, name: axis.name, values: 'values' in read ? read.values : [] }
      }),
      held_out: Number(draft.heldOut),
      walk_forward: windowsOf(draft.walkForward),
    }
  } else {
    body.baseline = { repetitions: Number(draft.repetitions), seed: Number(draft.seed) }
  }
  return body
}
