import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  Button,
  Checkbox,
  cn,
  Combobox,
  DateRangePicker,
  formatDay,
  formatMoney,
  formatNumber,
  Input,
  Select,
  toIsoDay,
} from 'ioiti-ui'
import type { DateRange } from 'ioiti-ui'
import { Plus, RotateCcw, Trash2 } from 'lucide-react'
import type { IPineInput } from 'pinets'
import { useId } from 'react'
import type { ReactNode } from 'react'

import { BarsFields, ScriptChoice, ScriptSettings } from '@/features/ta/signals/RuleFields'
import type { ScriptInputs } from '@/features/ta/signals/useScriptInputs'
import { barsLabel } from '@/features/ta/signals/ruleModel'
import type { RuleDraft } from '@/features/ta/signals/ruleModel'
import { api } from '@/lib/api'
import type { BacktestCoverage, BacktestCoverageDay, BacktestField, BacktestOptions, DataAsset, TaRule, TaScriptSummary } from '@/lib/types'

import { executionChanges, executionSentence } from './execution'
import {
  axisValues,
  changesOf,
  draftBars,
  fieldError,
  parseAsset,
  periodDays,
  periodLimit,
  rangeHint,
  strategyAssets,
  sweepCells,
  walkForwardChoices,
} from './form'
import type { AxisDraft, BacktestDraft } from './form'
import { assetLabel, settingChanges, showValue } from './model'
import { SettingChanges } from './parts'
import { measureSentence, underlyingLabel } from './turboModel'

type Update = (changes: Partial<BacktestDraft>) => void

function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-xs font-medium uppercase tracking-wide text-text-secondary">{title}</h3>
        {description && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  )
}

// ── 1. the asset and its bars ──

export function AssetStep({
  draft,
  update,
  updateRule,
  assets,
  rules,
  onRule,
}: {
  draft: BacktestDraft
  update: Update
  updateRule: (changes: Partial<RuleDraft>) => void
  assets: readonly DataAsset[]
  rules: readonly TaRule[]
  onRule: (rule: TaRule) => void
}) {
  const offered = strategyAssets(assets)
  const chosen = parseAsset(draft.asset)
  const assetFieldId = useId()
  const tick = assets.find((asset) => asset.ref === draft.asset)?.tick
  return (
    <div className="space-y-5 text-sm">
      {rules.length > 0 && (
        <Section
          title="Start from a signal rule"
          description="Its script, inputs and alerts, its asset and its bars, as the Signal rules page has them: everything can still be changed."
        >
          <div className="max-w-md">
            <Select
              aria-label="Signal rule to start from"
              value={draft.fromRule?.id ?? ''}
              onChange={(event) => {
                const rule = rules.find((entry) => entry.id === event.target.value)
                if (rule) onRule(rule)
              }}
              options={[{ value: '', label: 'None' }, ...rules.map((rule) => ({ value: rule.id, label: rule.name }))]}
            />
          </div>
        </Section>
      )}
      <Section
        title="Asset and bars"
        description="What the strategy reads: an asset of the Data pages, as time bars or range bars. A replay of the signals WATA received draws its days on it."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {/* A label beside the field, not around it: a label passes an option's click on to its field. */}
          <div className="flex flex-col gap-1 sm:col-span-2">
            <label htmlFor={assetFieldId} className="text-xs text-text-secondary">
              Asset
            </label>
            <Combobox
              id={assetFieldId}
              className="w-full"
              value={draft.asset || null}
              onValueChange={(value) => value && update({ asset: value })}
              options={offered.some((option) => option.value === draft.asset) || !chosen ? offered : [{ value: draft.asset, label: assetLabel(chosen) }, ...offered]}
              placeholder="Choose an asset"
              searchPlaceholder="A name or a symbol…"
              emptyText="No asset of that name."
            />
          </div>
          <BarsFields draft={draft.rule} update={updateRule} rangeNote={rangeHint(draft.rule.ticks, tick)} />
        </div>
        {chosen?.provider === 'saxo' && (
          <p className="text-xs text-muted-foreground">Saxo quotes the index only from 15:30 to 22:15: the strategy has no bar before.</p>
        )}
      </Section>
    </div>
  )
}

// ── 2. the strategy ──

export function StrategyStep({
  draft,
  update,
  updateRule,
  scripts,
  rules,
  onRule,
  options,
  inputs,
  invalidInputs,
  setInvalidInputs,
}: {
  draft: BacktestDraft
  update: Update
  updateRule: (changes: Partial<RuleDraft>) => void
  scripts: TaScriptSummary[]
  rules: readonly TaRule[]
  onRule: (rule: TaRule) => void
  options: BacktestOptions
  inputs: ScriptInputs
  invalidInputs: Record<string, string>
  setInvalidInputs: (change: (current: Record<string, string>) => Record<string, string>) => void
}) {
  const runnable = scripts.filter((script) => script.kind !== 'library')
  return (
    <div className="space-y-5 text-sm">
      <Section title="What makes the signals">
        <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="What makes the signals">
          {(
            [
              ['pine', 'A Pine script', 'Run as the TA engine runs a signal rule: day by day, each alert counting once its bar closed.'],
              ['journal', 'The signals WATA received', 'TradingView’s, as the trader received them, each beside what WATA did of it.'],
              ['ta_signals', 'The signals a rule raised', 'As the TA engine journaled them, live or in shadow: no script is run again. What paper trading trades.'],
            ] as const
          ).map(([kind, title, description]) => (
            <label
              key={kind}
              className={cn(
                'flex cursor-pointer flex-col gap-1 rounded-xl border p-3',
                draft.sourceKind === kind ? 'border-foreground/40 bg-surface/60 accent:border-brand' : 'border-border/70',
              )}
            >
              <span className="flex items-center gap-2 font-medium">
                <input type="radio" name="source" checked={draft.sourceKind === kind} onChange={() => update({ sourceKind: kind })} />
                {title}
              </span>
              <span className="text-xs text-muted-foreground">{description}</span>
            </label>
          ))}
        </div>
      </Section>

      <label className="flex max-w-xs flex-col gap-1">
        <span className="text-xs text-text-secondary">Indice traded (the turbos of)</span>
        <Select
          value={draft.indice}
          onChange={(event) => update({ indice: event.target.value })}
          options={options.indices.map((indice) => ({ value: indice, label: indice }))}
        />
      </label>

      {draft.sourceKind === 'pine' ? (
        runnable.length === 0 ? (
          <Alert tone="neutral">
            <AlertDescription>No indicator or strategy to run yet: write one on the Indicators page.</AlertDescription>
          </Alert>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <ScriptChoice draft={draft.rule} update={updateRule} scripts={scripts} onScriptChange={() => setInvalidInputs(() => ({}))} />
            </div>
            <ScriptSettings
              draft={draft.rule}
              update={updateRule}
              inputs={inputs}
              invalidInputs={invalidInputs}
              setInvalidInputs={setInvalidInputs}
            />
            <p className="text-xs text-muted-foreground">
              Only alerts make signals: the orders of a Pine strategy() are not read. A signal reaches the trader{' '}
              {options.signal_delay.pine} s after its bar closed (a setting of the last step).
            </p>
          </>
        )
      ) : draft.sourceKind === 'ta_signals' ? (
        <RaisedSignals draft={draft} rules={rules} onRule={onRule} />
      ) : (
        <p className="text-xs leading-relaxed text-muted-foreground">
          The journal of the period is read when the backtest is asked for. The scheduler’s close of the day is left out:
          each day closes at the run’s own close time.
        </p>
      )}
    </div>
  )
}

/** The rule whose signals are traded: its asset, bars and indice come with it, its script is not run again. */
function RaisedSignals({ draft, rules, onRule }: { draft: BacktestDraft; rules: readonly TaRule[]; onRule: (rule: TaRule) => void }) {
  const chosen = draft.fromRule
  // A rule deleted since keeps its signals: a run made again from one still names it.
  const options = [
    ...(chosen && !rules.some((rule) => rule.id === chosen.id) ? [{ value: chosen.id, label: `${chosen.name} (deleted)` }] : []),
    ...rules.map((rule) => ({ value: rule.id, label: rule.name })),
  ]
  return (
    <div className="space-y-2">
      <label className="flex max-w-md flex-col gap-1">
        <span className="text-xs text-text-secondary">The rule whose signals are traded</span>
        <Select
          value={chosen?.id ?? ''}
          onChange={(event) => {
            const rule = rules.find((entry) => entry.id === event.target.value)
            if (rule) onRule(rule)
          }}
          options={[...(chosen ? [] : [{ value: '', label: 'Choose a rule' }]), ...options]}
        />
      </label>
      <p className="text-xs leading-relaxed text-muted-foreground">
        The signals it raised in the period are read when the backtest is asked for, each reaching the trader as the
        journal says (a setting of the last step). Its asset and bars, of the first step, only draw the days and measure
        the edge.
      </p>
    </div>
  )
}

// ── 3. the period ──

const DAY_STATE: Record<string, { label: string; className: string }> = {
  ready: { label: 'turbos and bars recorded', className: 'bg-positive' },
  turbos: { label: 'turbos recorded, no bars of the asset', className: 'bg-warning' },
  bars: { label: 'bars of the asset, no turbo recorded', className: 'bg-muted-foreground/40' },
  nothing: { label: 'nothing recorded', className: 'border border-border' },
}

function dayState(day: BacktestCoverageDay): keyof typeof DAY_STATE {
  const turbos = day.turbos.long + day.turbos.short > 0
  const bars = day.asset_bars === null || day.asset_bars > 0
  if (turbos && bars) return 'ready'
  if (turbos) return 'turbos'
  return day.asset_bars ? 'bars' : 'nothing'
}

function isWeekend(day: string): boolean {
  const weekday = new Date(`${day}T12:00:00Z`).getUTCDay()
  return weekday === 0 || weekday === 6
}

/** What was recorded each day of the period: one square a day, weekends left out; the entry signals, received or raised. */
export function RecordedDays({ coverage, signals }: { coverage: BacktestCoverage; signals: 'received' | 'raised' | null }) {
  const days = coverage.days.filter((day) => !isWeekend(day.day) || day.turbos.long + day.turbos.short > 0)
  return (
    <div className="space-y-2">
      <ul className="flex flex-wrap gap-1.5" aria-label="Days of the period">
        {days.map((day) => {
          const state = DAY_STATE[dayState(day)]
          const detail = [
            `${day.turbos.long} long and ${day.turbos.short} short turbos`,
            day.asset_bars === null ? null : `${formatNumber(day.asset_bars)} bars of the asset`,
            `the recorder listened ${formatNumber(day.recorded_minutes)} min`,
            signals && day.signals !== null ? `${day.signals} signal${day.signals === 1 ? '' : 's'} of entry ${signals}` : null,
          ].filter(Boolean).join(', ')
          return (
            <li key={day.day} title={`${formatDay(day.day)}: ${state.label} (${detail})`} className={cn('size-4 rounded-[4px]', state.className)}>
              <span className="sr-only">
                {formatDay(day.day)}: {state.label} ({detail})
              </span>
            </li>
          )
        })}
      </ul>
      <ul className="flex flex-wrap gap-4 text-xs text-muted-foreground" aria-hidden>
        {Object.entries(DAY_STATE).map(([name, state]) => (
          <li key={name} className="flex items-center gap-1.5">
            <span className={cn('size-3 rounded-[3px]', state.className)} />
            {state.label}
          </li>
        ))}
      </ul>
      {!coverage.database && (
        <p className="text-xs text-warning">The database cannot be read: what the recorder kept is not known here.</p>
      )}
    </div>
  )
}

export function PeriodStep({
  draft,
  update,
  options,
  coverage,
  coveragePending,
}: {
  draft: BacktestDraft
  update: Update
  options: BacktestOptions
  coverage: BacktestCoverage | undefined
  coveragePending: boolean
}) {
  const range: DateRange = {
    from: draft.from ? new Date(`${draft.from}T12:00:00`) : null,
    to: draft.to ? new Date(`${draft.to}T12:00:00`) : null,
  }
  const ready = coverage?.days.filter((day) => dayState(day) === 'ready').length ?? 0
  const modelled = draft.turbos === 'modelled'
  return (
    <div className="space-y-5 text-sm">
      <Section
        title="Turbos"
        description="Those the price recorder kept, or turbos made up from the index on any day it has, priced as the recorded ones were: years of days, at the cost of a model."
      >
        <div className="flex flex-wrap gap-2" role="group" aria-label="Turbos traded">
          <Button size="sm" variant={modelled ? 'outline' : 'primary'} aria-pressed={!modelled} onClick={() => update({ turbos: 'recorded' })}>
            Recorded by WATA
          </Button>
          <Button
            size="sm"
            variant={modelled ? 'primary' : 'outline'}
            aria-pressed={modelled}
            disabled={!options.turbo_model}
            onClick={() => update({ turbos: 'modelled' })}
          >
            Made up from the index
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">{measureSentence(options.turbo_model)}</p>
      </Section>
      <Section
        title="Days to trade"
        description={
          modelled
            ? `Local days of ${options.timezone}, ${periodLimit(draft, options)} at most. Every day the index has prices for is traded.`
            : `Local days of ${options.timezone}, ${options.limits.period_days} at most. Only the days the price recorder kept turbos of can be traded.`
        }
      >
        <DateRangePicker
          value={range}
          onChange={(chosen) =>
            update({ from: chosen.from ? toIsoDay(chosen.from) : '', to: chosen.to ? toIsoDay(chosen.to) : chosen.from ? toIsoDay(chosen.from) : '' })
          }
          placeholder="Period"
          align="start"
          locale="en-GB"
          weekStartsOn={1}
        />
        {modelled ? null : coverage ? (
          <>
            <p className="text-sm">
              {ready} day{ready === 1 ? '' : 's'} of the {periodDays(draft.from, draft.to)} can be traded.
            </p>
            <RecordedDays
              coverage={coverage}
              signals={draft.sourceKind === 'journal' ? 'received' : draft.sourceKind === 'ta_signals' ? 'raised' : null}
            />
          </>
        ) : (
          coveragePending && <p className="text-xs text-muted-foreground">Reading what was recorded…</p>
        )}
      </Section>
    </div>
  )
}

// ── 4. the settings ──

const GROUP_LABEL: Record<string, { title: string; description?: string }> = {
  entries: { title: 'Which signals are taken', description: 'The trader’s checks on an entry, in its order.' },
  day: { title: 'The day' },
  exits: { title: 'What sells a position', description: 'The position monitor’s, on the turbo’s bid.' },
  turbo: { title: 'The turbo bought', description: 'The lowest bid in this range, among the twenty closest to their knock-out level.' },
  sizing: { title: 'How much is bought' },
  execution: {
    title: 'How the backtest fills its orders',
    description: 'Not settings of WATA: what a backtest assumes of the time things take and of what they cost.',
  },
  engine: { title: 'How the script is run', description: 'The TA engine’s: each day of bars from a restart to the next.' },
}

function FieldInput({ field, value, onChange }: { field: BacktestField; value: string; onChange: (text: string) => void }) {
  if (field.kind === 'boolean') {
    return (
      <Select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        options={[
          { value: 'true', label: 'On' },
          { value: 'false', label: 'Off' },
        ]}
      />
    )
  }
  if (field.kind === 'choice') {
    return <Select value={value} onChange={(event) => onChange(event.target.value)} options={field.choices.map((choice) => ({ value: choice, label: choice }))} />
  }
  return (
    <Input
      value={value}
      type={field.kind === 'time' ? 'text' : 'number'}
      inputMode={field.kind === 'time' ? 'numeric' : 'decimal'}
      placeholder={field.kind === 'time' ? 'HH:MM' : undefined}
      step={field.kind === 'integer' ? 1 : 'any'}
      min={field.least ?? undefined}
      max={field.most ?? undefined}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

/** What WATA's own orders took and cost, and those values put in the form at a click. */
function ExecutionMeasured({ draft, update, fields }: { draft: BacktestDraft; update: Update; fields: readonly BacktestField[] }) {
  const measuredQuery = useQuery({ queryKey: ['backtest-execution'], queryFn: api.backtestExecution, staleTime: 300_000 })
  const measured = measuredQuery.data
  if (!measured) return null
  const changes = executionChanges(measured, draft.sourceKind, fields.map((field) => field.name))
  const already = Object.entries(changes).every(([name, value]) => draft.changes[name] === value)
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg bg-foreground/[0.03] px-3 py-2 text-xs">
      <span className="min-w-0 flex-1 text-muted-foreground">{executionSentence(measured, draft.sourceKind)}</span>
      {Object.keys(changes).length > 0 && (
        <Button size="sm" variant="outline" disabled={already} onClick={() => update({ changes: { ...draft.changes, ...changes } })}>
          {already ? 'Measured values in use' : 'Use the measured values'}
        </Button>
      )}
    </div>
  )
}

export function SettingsStep({
  draft,
  update,
  options,
  meta,
}: {
  draft: BacktestDraft
  update: Update
  options: BacktestOptions
  meta: readonly IPineInput[] | null
}) {
  const groups = options.groups.filter((group) => group !== 'engine' || draft.sourceKind === 'pine')
  const setText = (field: BacktestField, text: string) => {
    const { [field.name]: _dropped, ...others } = draft.changes
    update({ changes: text === String(field.value) ? others : { ...others, [field.name]: text } })
  }
  return (
    <div className="space-y-7 text-sm">
      {groups.map((group) => (
        <Section key={group} title={GROUP_LABEL[group]?.title ?? group} description={GROUP_LABEL[group]?.description}>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {options.fields
              .filter((field) => field.group === group)
              .map((field) => {
                const text = draft.changes[field.name] ?? String(field.value)
                const changed = field.name in draft.changes
                const error = changed ? fieldError(field, text) : null
                return (
                  <label key={field.name} className="flex flex-col gap-1">
                    <span className="text-xs text-text-secondary">
                      {field.label}
                      {field.unit && field.kind !== 'boolean' ? ` (${field.unit})` : ''}
                    </span>
                    <FieldInput field={field} value={text} onChange={(next) => setText(field, next)} />
                    <span className={cn('text-xs', error ? 'text-negative' : 'text-muted-foreground')}>
                      {error ??
                        (changed ? (
                          <>
                            {field.source ? 'Live' : 'By default'}: {showValue(field.value, field)}{' '}
                            <button type="button" className="underline underline-offset-2" onClick={() => setText(field, String(field.value))}>
                              put it back
                            </button>
                          </>
                        ) : field.source ? (
                          'As live'
                        ) : field.name === 'capital' && options.capital.balance !== null ? (
                          <>
                            The backtest’s default; the live account holds {formatMoney(options.capital.balance, 'EUR')}:{' '}
                            <button
                              type="button"
                              className="underline underline-offset-2"
                              onClick={() => setText(field, String(options.capital.balance))}
                            >
                              start from it
                            </button>
                          </>
                        ) : (
                          'The backtest’s default'
                        ))}
                    </span>
                  </label>
                )
              })}
          </div>
          {group === 'execution' && <ExecutionMeasured draft={draft} update={update} fields={options.fields} />}
          {group === 'sizing' && (
            <p className="text-xs text-muted-foreground">
              Hours and confidence bands, as the configuration sets them:{' '}
              {options.fixed.time_periods.map((period) => `${period.label || 'from'} ${String(Math.floor(period.start_minute / 60)).padStart(2, '0')}:${String(period.start_minute % 60).padStart(2, '0')} ${period.scale_percent} %`).join(', ') || 'none'}
              ; {options.fixed.confidence_bands.map((band) => `${band.least}–${band.below}: ${band.scale_percent} %`).join(', ') || 'no band'}.
            </p>
          )}
        </Section>
      ))}

      <Section
        title="Set against random entries, or sweep"
        description="A run is traded again with entries at random to tell what its entries are worth. A sweep tries several values of one or two settings instead, with the last days held out of the choice."
      >
        <Checkbox
          label="Try several values (a sweep)"
          checked={draft.sweep}
          onChange={(event) => update({ sweep: event.target.checked, axes: event.target.checked && draft.axes.length === 0 ? [{ kind: 'setting', name: 'stoploss_percent', values: '' }] : draft.axes })}
        />
        {draft.sweep ? (
          <SweepAxes draft={draft} update={update} options={options} meta={meta} />
        ) : (
          <div className="grid max-w-md gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-secondary">Repetitions at random (0: none)</span>
              <Input type="number" min={0} max={options.baseline.most} step={1} value={draft.repetitions} onChange={(event) => update({ repetitions: event.target.value })} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-secondary">Seed of the draws</span>
              <Input type="number" min={0} step={1} value={draft.seed} onChange={(event) => update({ seed: event.target.value })} />
            </label>
          </div>
        )}
      </Section>
    </div>
  )
}

function SweepAxes({
  draft,
  update,
  options,
  meta,
}: {
  draft: BacktestDraft
  update: Update
  options: BacktestOptions
  meta: readonly IPineInput[] | null
}) {
  const fieldId = useId()
  const setAxis = (index: number, changes: Partial<AxisDraft>) =>
    update({ axes: draft.axes.map((axis, place) => (place === index ? { ...axis, ...changes } : axis)) })
  const read = draft.axes.map((axis) => axisValues(axis, options.fields, meta, options.sweep.values))
  const cells = sweepCells(read.map((entry) => ({ values: 'values' in entry ? entry.values : [] })))
  return (
    <div className="space-y-3">
      {draft.axes.map((axis, index) => {
        const names =
          axis.kind === 'setting'
            ? options.fields
                .filter((field) => field.group !== 'engine' || draft.sourceKind === 'pine')
                .map((field) => ({ value: field.name, label: field.label, keywords: [field.name] }))
            : (meta ?? []).map((input) => ({ value: input.id, label: input.title ?? input.name ?? input.id }))
        const entry = read[index]
        return (
          <div key={index} className="grid items-end gap-3 sm:grid-cols-[10rem_1fr_1fr_auto]">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-secondary">Axis {index + 1}</span>
              <Select
                value={axis.kind}
                onChange={(event) => setAxis(index, { kind: event.target.value === 'input' ? 'input' : 'setting', name: '' })}
                options={[
                  { value: 'setting', label: 'A setting' },
                  ...(draft.sourceKind === 'pine' ? [{ value: 'input', label: 'An input of the script' }] : []),
                ]}
              />
            </label>
            {/* Dozens of settings: found by typing. A label beside, not around: it would pass an option's click on to the field. */}
            <div className="flex flex-col gap-1">
              <label htmlFor={`${fieldId}-${index}`} className="text-xs text-text-secondary">
                Which
              </label>
              <Combobox
                id={`${fieldId}-${index}`}
                className="w-full"
                value={axis.name || null}
                onValueChange={(value) => setAxis(index, { name: value ?? '' })}
                options={names}
                placeholder="Choose…"
                searchPlaceholder="A setting or an input…"
                emptyText="Nothing of that name."
              />
            </div>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-secondary">Values, separated by commas</span>
              <Input value={axis.values} placeholder="-8, -12, -15" onChange={(event) => setAxis(index, { values: event.target.value })} />
              {'error' in entry && axis.values.trim() !== '' && <span className="text-xs text-negative">{entry.error}</span>}
            </label>
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Remove axis ${index + 1}`}
              leftIcon={<Trash2 className="size-3.5" />}
              onClick={() => update({ axes: draft.axes.filter((_, place) => place !== index) })}
            >
              Remove
            </Button>
          </div>
        )
      })}
      <div className="flex flex-wrap items-center gap-3">
        {draft.axes.length < options.sweep.axes && (
          <Button
            variant="outline"
            size="sm"
            leftIcon={<Plus className="size-3.5" />}
            onClick={() => update({ axes: [...draft.axes, { kind: 'setting', name: '', values: '' }] })}
          >
            Add an axis
          </Button>
        )}
        <label className="flex items-center gap-2 text-xs text-text-secondary">
          Days held out
          <Select
            value={draft.heldOut}
            onChange={(event) => update({ heldOut: event.target.value })}
            options={[0.2, 0.3, 0.4, 0.5].map((share) => ({ value: String(share), label: `the last ${share * 100} %` }))}
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-text-secondary">
          Walk-forward
          <Select value={draft.walkForward} onChange={(event) => update({ walkForward: event.target.value })} options={walkForwardChoices(draft.walkForward)} />
        </label>
        <span className="text-xs text-muted-foreground">
          {cells} combination{cells === 1 ? '' : 's'}, {options.sweep.cells} at most.
        </span>
      </div>
    </div>
  )
}

// ── 5. the review ──

/** What makes a run's signals, as the review names it. */
function signalsLabel(draft: BacktestDraft, script: TaScriptSummary | undefined): string {
  if (draft.sourceKind === 'journal') return 'The signals WATA received'
  if (draft.sourceKind === 'ta_signals') return `The signals ${draft.fromRule?.name ?? 'a rule'} raised, as the engine journaled them`
  return `${script?.name ?? 'Script'} v${draft.rule.version}${draft.fromRule ? ` (from the rule ${draft.fromRule.name})` : ''}`
}

export function ReviewStep({
  draft,
  update,
  options,
  scripts,
  coverage,
  problems,
}: {
  draft: BacktestDraft
  update: Update
  options: BacktestOptions
  scripts: TaScriptSummary[]
  coverage: BacktestCoverage | undefined
  problems: string[]
}) {
  const asset = parseAsset(draft.asset)
  const script = scripts.find((entry) => entry.id === draft.rule.scriptId)
  const changes = settingChanges(changesOf(draft, options.fields), {}, options.fields)
  const ready = coverage?.days.filter((day) => dayState(day) === 'ready').length ?? null
  return (
    <div className="space-y-5 text-sm">
      <dl className="divide-y divide-border rounded-lg border border-border">
        {[
          ['Signals', signalsLabel(draft, script)],
          ['Asset', asset ? `${assetLabel(asset)} · ${barsLabel(draftBars(draft))} · ${draft.rule.side}` : '-'],
          ['Turbos of', `${draft.indice}, ${draft.turbos === 'modelled' && options.turbo_model ? `made up from ${underlyingLabel(options.turbo_model.underlying)}` : 'as recorded'}`],
          ['Period', draft.from && draft.to ? `${formatDay(draft.from)} to ${formatDay(draft.to)}${ready !== null ? `: ${ready} day${ready === 1 ? '' : 's'} can be traded` : ''}` : '-'],
          ['Against random', draft.sweep ? 'No: a sweep' : Number(draft.repetitions) > 0 ? `${draft.repetitions} repetitions` : 'No'],
        ].map(([label, value]) => (
          <div key={label} className="grid gap-1 px-3 py-2 sm:grid-cols-[9rem_1fr]">
            <dt className="text-text-secondary">{label}</dt>
            <dd className="min-w-0 break-words">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="space-y-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-text-secondary">Changed from the live settings</h3>
        <SettingChanges label="Changed from the live settings" changes={changes} none="Nothing: WATA’s settings as they are live." />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-text-secondary">Name (optional)</span>
          <Input value={draft.name} maxLength={options.limits.name_length} onChange={(event) => update({ name: event.target.value })} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-text-secondary">Tags, separated by commas</span>
          <Input value={draft.tags} onChange={(event) => update({ tags: event.target.value })} />
        </label>
      </div>
      {problems.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-xs text-negative">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {ready === 0 && (
        <Alert tone="warning">
          <AlertDescription>
            No day of this period has turbos and bars recorded: the run would have nothing to trade. Choose days the price
            recorder ran.
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}

export function ResetLink({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" leftIcon={<RotateCcw className="size-3.5" />} onClick={onClick}>
      Start again
    </Button>
  )
}
