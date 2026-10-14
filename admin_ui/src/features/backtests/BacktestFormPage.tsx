import { skipToken, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Play } from 'lucide-react'
import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router'

import { PageHeader, Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import type { DateRange } from '@/components/ui/calendar'
import { DateRangePicker } from '@/components/ui/date-picker'
import { FormControl, FormDescription, FormItem, FormLabel, FormMessage, Input } from '@/components/ui/form'
import { SpringSelect } from '@/components/ui/spring-select'
import { StretchSwitch } from '@/components/ui/stretch-switch'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { lastDays, rangeParams } from '@/lib/dateRange'
import { formatDay } from '@/lib/format'
import type {
  BacktestOptions,
  BacktestRun,
  BacktestSignalsRequest,
  BacktestValue,
  PriceCoverage,
  SweepAxis,
} from '@/lib/types'
import { cn } from '@/lib/utils'

import {
  axisValues,
  dayToDate,
  fieldError,
  isReplay,
  leveragesOf,
  middleLeverage,
  numberOf,
  showValue,
  typed,
  wording,
} from './model'
import type { FormValues } from './model'
import { BacktestsDisabled } from './parts'

const DEFAULT_PERIOD_DAYS = 30

interface FieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  /** Why the value cannot be used, when it cannot. */
  error?: string | null
  help?: string
  /** What the value is when left alone, said once it has been changed: "Default: 15", "Live: 1.7". */
  unchanged?: string
  choices?: readonly string[]
  numeric?: boolean
}

function Field({ label, value, onChange, error, help, unchanged, choices, numeric }: FieldProps) {
  const id = useId()
  const note = unchanged && <p className="text-xs text-warning">{unchanged}</p>

  if (choices && choices.length > 0) {
    return (
      <div className="flex flex-col gap-1.5">
        <SpringSelect
          label={label}
          options={choices.map((choice) => ({ value: choice, label: choice }))}
          value={value}
          onChange={onChange}
        />
        {note}
        {help && <p className="text-xs text-muted-foreground">{help}</p>}
      </div>
    )
  }
  return (
    <FormItem>
      <FormLabel htmlFor={id}>{label}</FormLabel>
      <FormControl>
        <Input
          id={id}
          inputMode={numeric ? 'decimal' : undefined}
          value={value}
          error={!!error}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(event) => onChange(event.target.value)}
        />
      </FormControl>
      {error && <FormMessage id={`${id}-error`}>{error}</FormMessage>}
      {note}
      {help && <FormDescription>{help}</FormDescription>}
    </FormItem>
  )
}

function Fields({ children }: { children: ReactNode }) {
  return <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2 lg:grid-cols-3">{children}</div>
}

/** The last month that has prices, or the last thirty days when nothing was fetched yet. */
function defaultPeriod(coverage: PriceCoverage): DateRange {
  if (!coverage.ready.last || !coverage.ready.first) return lastDays(DEFAULT_PERIOD_DAYS)
  const to = dayToDate(coverage.ready.last)
  const from = new Date(to)
  from.setDate(to.getDate() - (DEFAULT_PERIOD_DAYS - 1))
  const first = dayToDate(coverage.ready.first)
  return { from: from < first ? first : from, to }
}

/** Weekdays of the period, among those the coverage knows of, that have no complete prices. */
function daysWithoutPrices(coverage: PriceCoverage, from: string, to: string): number {
  return coverage.days.filter(
    (day) => day.day >= from && day.day <= to && (day.state === 'missing' || day.state === 'incomplete'),
  ).length
}

type Replayed = 'strategy' | 'signals'

const REPLAYED: { value: Replayed; label: string; help: string }[] = [
  {
    value: 'strategy',
    label: 'A strategy',
    help: 'Its own signals, computed on the prices: what it would have asked WATA to do.',
  },
  {
    value: 'signals',
    label: 'The signals WATA received',
    help: "The signals in WATA's journal for the period, run through the same model. Its trades are then set beside the real ones: it is how the model itself is checked.",
  },
]

/** A setting a sweep can give several values to, as the form offers it. */
interface Setting {
  /** "rule:day_profit_target": what the choice is kept as. */
  key: string
  kind: 'parameter' | 'rule'
  name: string
  label: string
  field: { kind: string; least: number | null; most: number | null; choices?: readonly string[] }
}

const NO_SETTING = ''

interface TypedAxis {
  setting: string
  values: string
}

interface FormProps {
  options: BacktestOptions
  coverage: PriceCoverage
  /** A run to start from: "run again with changes". */
  source: BacktestRun | null
}

function BacktestForm({ options, coverage, source }: FormProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const strategy = options.strategies.find((candidate) => candidate.name === source?.strategy) ?? options.strategies[0]
  const live = options.rules.live
  const numberRules = options.rules.editable.filter((rule) => rule.kind !== 'boolean')
  const switchRules = options.rules.editable.filter((rule) => rule.kind === 'boolean')
  const turbo = source?.turbo ?? options.turbo

  const [range, setRange] = useState<DateRange>(() =>
    source ? { from: dayToDate(source.from), to: dayToDate(source.to) } : defaultPeriod(coverage),
  )
  const [parameters, setParameters] = useState<FormValues>(() =>
    Object.fromEntries(
      strategy.parameters.map((parameter) => [parameter.name, String(source?.parameters[parameter.name] ?? parameter.default)]),
    ),
  )
  const [rules, setRules] = useState<FormValues>(() =>
    Object.fromEntries(numberRules.map((rule) => [rule.name, String(source?.rules[rule.name] ?? live[rule.name])])),
  )
  const [switches, setSwitches] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(switchRules.map((rule) => [rule.name, Boolean(source?.rules[rule.name] ?? live[rule.name])])),
  )
  const [entryPrice, setEntryPrice] = useState(String(turbo.entry_price))
  const [spread, setSpread] = useState(String(turbo.spread))
  const [leverages, setLeverages] = useState(turbo.leverages.join(', '))
  const [note, setNote] = useState(source?.note ?? '')
  const [replayed, setReplayed] = useState<Replayed>(
    source && (isReplay(source) || source.replays === 'signals') ? 'signals' : 'strategy',
  )
  const replaying = replayed === 'signals'
  const execution = source?.execution ?? options.execution
  // A sweep counts no money: started from one, the form takes the usual account again.
  const [capital, setCapital] = useState(String(execution.capital || options.execution.capital))
  const [fillDelay, setFillDelay] = useState(String(execution.fill_delay))
  const [slippage, setSlippage] = useState(String(execution.slippage))
  const [axes, setAxes] = useState<[TypedAxis, TypedAxis]>(() => {
    const from = (number: number): TypedAxis => {
      const axis = source?.axes?.[number]
      return axis ? { setting: `${axis.kind}:${axis.name}`, values: axis.values.map(showValue).join(', ') } : { setting: NO_SETTING, values: '' }
    }
    return [from(0), from(1)]
  })
  const [heldOut, setHeldOut] = useState(String(Math.round((source?.held_out ?? options.held_out.share) * 100)))
  const [sweepLeverage, setSweepLeverage] = useState<string | null>(null)
  // The parameters seldom touched stay folded unless the run started from changed one of them.
  const [more, setMore] = useState(() =>
    strategy.parameters.some((parameter) => parameter.advanced && source?.parameters[parameter.name] !== undefined),
  )

  const period = rangeParams(range)
  const parameterErrors = Object.fromEntries(
    strategy.parameters.map((parameter) => [parameter.name, fieldError(parameters[parameter.name], parameter)]),
  )
  const ruleErrors = Object.fromEntries(numberRules.map((rule) => [rule.name, fieldError(rules[rule.name], rule)]))
  const entryPriceValue = numberOf(entryPrice)
  const spreadValue = numberOf(spread)
  const leverageValues = leveragesOf(leverages)
  const entryPriceError = entryPriceValue === null || entryPriceValue <= 0 ? 'A price above zero.' : null
  const spreadError = spreadValue === null || spreadValue < 0 ? 'Zero or more.' : null
  const leveragesError =
    leverageValues === null
      ? 'Numbers above zero separated by commas, each once.'
      : leverageValues.length > options.limits.leverages
        ? `${options.limits.leverages} at most.`
        : null
  const capitalValue = numberOf(capital)
  const fillDelayValue = numberOf(fillDelay)
  const slippageValue = numberOf(slippage)
  const capitalError = capitalValue === null || capitalValue < 0 ? 'An amount, 0 for no money figures.' : null
  const fillDelayError =
    fillDelayValue === null || !Number.isInteger(fillDelayValue) || fillDelayValue < 0 || fillDelayValue > options.limits.fill_delay
      ? `Whole seconds, from 0 to ${options.limits.fill_delay}.`
      : null
  const slippageError =
    slippageValue === null || slippageValue < 0 || slippageValue > options.limits.slippage
      ? `From 0 to ${options.limits.slippage} index points.`
      : null

  // What a sweep can give several values to: numbers, switches, and the parameters that are a choice.
  const settings: Setting[] = [
    ...(replaying
      ? []
      : strategy.parameters
          .filter((parameter) => parameter.kind !== 'text' || parameter.choices.length > 1)
          .map((parameter): Setting => ({
            key: `parameter:${parameter.name}`, kind: 'parameter', name: parameter.name, label: wording(parameter.name), field: parameter,
          }))),
    ...options.rules.editable.map((rule): Setting => ({
      key: `rule:${rule.name}`, kind: 'rule', name: rule.name, label: rule.label, field: rule,
    })),
  ]
  const chosen = axes
    .map((axis) => ({ axis, setting: settings.find((candidate) => candidate.key === axis.setting) }))
    .filter((entry): entry is { axis: TypedAxis; setting: Setting } => !!entry.setting)
  const sweeping = chosen.length > 0
  const read = chosen.map(({ axis, setting }) => axisValues(axis.values, setting.field, options.limits.sweep_values))
  const sweptAxes: SweepAxis[] = chosen.flatMap(({ setting }, number) => {
    const values = read[number]
    return 'values' in values ? [{ kind: setting.kind, name: setting.name, values: values.values }] : []
  })
  const combinations = sweptAxes.reduce((count, axis) => count * axis.values.length, 1)
  const sameSetting = chosen.length === 2 && chosen[0].setting.key === chosen[1].setting.key
  const sweepError = !sweeping
    ? null
    : sameSetting
      ? 'The two settings are the same one.'
      : sweptAxes.length === chosen.length && combinations > options.limits.sweep_cells
        ? `${combinations} combinations: ${options.limits.sweep_cells} at most.`
        : null
  const sweepInvalid = sweeping && (sweptAxes.length !== chosen.length || !!sweepError)
  const sweptNames = new Set(sweptAxes.map((axis) => `${axis.kind}:${axis.name}`))
  // One turbo for a sweep: the one chosen, else the middle one of those typed.
  const sweepTurbo =
    leverageValues && leverageValues.length > 0
      ? (leverageValues.find((leverage) => String(leverage) === sweepLeverage) ?? middleLeverage(leverageValues))
      : null

  const invalid =
    !period.from ||
    !period.to ||
    // A replay of signals has no strategy whose parameters could be wrong.
    (!replaying && Object.values(parameterErrors).some(Boolean)) ||
    Object.values(ruleErrors).some(Boolean) ||
    !!entryPriceError ||
    !!spreadError ||
    !!leveragesError ||
    !!capitalError ||
    !!fillDelayError ||
    !!slippageError ||
    sweepInvalid

  // What a sweep gives several values to is not also sent as one value.
  const changedParameters = replaying
    ? []
    : strategy.parameters.filter(
        (parameter) =>
          !sweptNames.has(`parameter:${parameter.name}`) &&
          !parameterErrors[parameter.name] &&
          typed(parameters[parameter.name], parameter.kind) !== parameter.default,
      )
  const changedRules: [string, BacktestValue][] = [
    ...numberRules
      .filter((rule) => !ruleErrors[rule.name] && Number(rules[rule.name]) !== live[rule.name])
      .map((rule): [string, BacktestValue] => [rule.name, Number(rules[rule.name])]),
    ...switchRules
      .filter((rule) => switches[rule.name] !== live[rule.name])
      .map((rule): [string, BacktestValue] => [rule.name, switches[rule.name]]),
  ].filter(([name]) => !sweptNames.has(`rule:${name}`)) as [string, BacktestValue][]

  const createMutation = useMutation({
    mutationFn: () => {
      const body: BacktestSignalsRequest = {
        from: period.from ?? '',
        to: period.to ?? '',
        rules: Object.fromEntries(changedRules),
        entry_price: entryPriceValue ?? 0,
        spread: spreadValue ?? 0,
        leverages: leverageValues ?? [],
        note: note.trim(),
        capital: capitalValue ?? 0,
        fill_delay: fillDelayValue ?? 0,
        slippage: slippageValue ?? 0,
      }
      const changed = Object.fromEntries(
        changedParameters.map((parameter) => [parameter.name, typed(parameters[parameter.name], parameter.kind)]),
      )
      if (sweeping) {
        return api.createSweep({
          from: body.from,
          to: body.to,
          replay: replaying ? 'signals' : 'strategy',
          axes: sweptAxes,
          held_out: Number(heldOut) / 100,
          strategy: strategy.name,
          parameters: changed,
          rules: body.rules,
          entry_price: body.entry_price,
          spread: body.spread,
          leverage: sweepTurbo ?? 0,
          note: body.note,
          fill_delay: body.fill_delay,
          slippage: body.slippage,
        })
      }
      if (replaying) return api.replaySignals(body)
      return api.createBacktest({ ...body, strategy: strategy.name, parameters: changed })
    },
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      navigate(`/backtests/${run.id}`)
    },
  })

  const withoutPrices = period.from && period.to ? daysWithoutPrices(coverage, period.from, period.to) : 0
  const shown = strategy.parameters.filter((parameter) => more || !parameter.advanced)

  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault()
        if (!invalid) createMutation.mutate()
      }}
    >
      <Panel
        title="Period"
        description={
          coverage.ready.first
            ? `Prices are ready from ${formatDay(coverage.ready.first)} to ${formatDay(coverage.ready.last)}. Days are those of ${options.timezone}.`
            : undefined
        }
      >
        <div className="flex flex-col gap-3">
          <DateRangePicker
            value={range}
            onChange={setRange}
            placeholder="First and last day"
            align="start"
            locale="en-GB"
            weekStartsOn={1}
          />
          {!coverage.ready.first && (
            <Alert tone="warning">
              <AlertDescription>
                No day of prices has been fetched yet: a backtest would find nothing to replay. Fetch some from the
                Backtests page first.
              </AlertDescription>
            </Alert>
          )}
          {withoutPrices > 0 && (
            <p className="text-xs text-warning">
              {withoutPrices} weekday{withoutPrices === 1 ? '' : 's'} of this period{' '}
              {withoutPrices === 1 ? 'has' : 'have'} no complete prices: {withoutPrices === 1 ? 'it is' : 'they are'}{' '}
              left out, and the run says which.
            </p>
          )}
        </div>
      </Panel>

      <Panel title="What to replay">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2" role="group" aria-label="What to replay">
            {REPLAYED.map((choice) => (
              <Button
                key={choice.value}
                type="button"
                variant={choice.value === replayed ? 'primary' : 'outline'}
                size="sm"
                aria-pressed={choice.value === replayed}
                onClick={() => setReplayed(choice.value)}
              >
                {choice.label}
              </Button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">{REPLAYED.find((choice) => choice.value === replayed)?.help}</p>
        </div>
      </Panel>

      {!replaying && (
        <Panel title="Strategy" description={`${wording(strategy.name)}: what it enters and leaves on.`}>
          <div className="flex flex-col gap-5">
            <Fields>
              {shown.map((parameter) => (
                <Field
                  key={parameter.name}
                  label={wording(parameter.name)}
                  value={parameters[parameter.name]}
                  onChange={(value) => setParameters((current) => ({ ...current, [parameter.name]: value }))}
                  error={parameterErrors[parameter.name]}
                  help={parameter.help}
                  choices={parameter.choices}
                  numeric={parameter.kind !== 'text'}
                  unchanged={
                    !parameterErrors[parameter.name] && typed(parameters[parameter.name], parameter.kind) !== parameter.default
                      ? `Default: ${showValue(parameter.default)}`
                      : undefined
                  }
                />
              ))}
            </Fields>
            <div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-expanded={more}
                rightIcon={<ChevronDown className={cn('size-3.5 transition-transform', more && 'rotate-180')} />}
                onClick={() => setMore((open) => !open)}
              >
                {more ? 'Fewer parameters' : 'More parameters'}
              </Button>
            </div>
          </div>
        </Panel>
      )}

      <Panel
        title="WATA's rules for this run"
        description="As the live configuration has them. Change one to see what it would have done: the configuration itself is not touched."
      >
        <div className="flex flex-col gap-5">
          <Fields>
            {numberRules.map((rule) => (
              <Field
                key={rule.name}
                label={rule.label}
                value={rules[rule.name]}
                onChange={(value) => setRules((current) => ({ ...current, [rule.name]: value }))}
                error={ruleErrors[rule.name]}
                numeric
                unchanged={
                  !ruleErrors[rule.name] && Number(rules[rule.name]) !== live[rule.name]
                    ? `Live: ${showValue(live[rule.name] as BacktestValue)}`
                    : undefined
                }
              />
            ))}
          </Fields>
          {switchRules.map((rule) => (
            <StretchSwitch
              key={rule.name}
              label={rule.label}
              description={
                switches[rule.name] !== live[rule.name] ? `Live: ${showValue(live[rule.name] as BacktestValue)}` : undefined
              }
              checked={switches[rule.name]}
              onChange={(checked) => setSwitches((current) => ({ ...current, [rule.name]: checked }))}
            />
          ))}
        </div>
      </Panel>

      <Panel
        title={options.turbo.measured ? 'Turbo measured on real trades' : 'Turbo assumed'}
        description={
          options.turbo.measured
            ? "The rules are about a turbo's price, which is modelled from the index. These values were measured on WATA's closed trades (Backtests page): the backtest is made once for the leverage found and once for each end of the range it could be in."
            : "The rules are about a turbo's price, which is modelled from the index. Its leverage is not known: the backtest is made once for each value given, and a conclusion should hold for all of them. It can be measured on WATA's closed trades from the Backtests page."
        }
      >
        <Fields>
          <Field label="Price paid" value={entryPrice} onChange={setEntryPrice} error={entryPriceError} numeric />
          <Field label="Spread (ask minus bid)" value={spread} onChange={setSpread} error={spreadError} numeric />
          <Field
            label="Index points for 1.00 of turbo"
            value={leverages}
            onChange={setLeverages}
            error={leveragesError}
            help="Several values, separated by commas."
          />
        </Fields>
      </Panel>

      <Panel
        title="Fills, and the account"
        description="A backtest fills every decision at the first price of the next ten seconds. Make it less kind to see what a result owes to that. The percentages count each trade as the whole stake; the money counts what WATA would really have put on it, by the time of day and the confidence, on an account that starts with the capital."
      >
        <Fields>
          <Field
            label="Wait before a fill, in seconds"
            value={fillDelay}
            onChange={setFillDelay}
            error={fillDelayError}
            numeric
            help="On top of the next ten seconds."
          />
          <Field
            label="Index points lost on each fill"
            value={slippage}
            onChange={setSlippage}
            error={slippageError}
            numeric
            help="Against the trade, at its entry and at its exit."
          />
          <Field
            label="Starting capital"
            value={capital}
            onChange={setCapital}
            error={capitalError}
            numeric
            help="In the account's currency. Not used by a sweep."
          />
        </Fields>
      </Panel>

      <Panel
        title="Try several values"
        description="Optional: a sweep. Give one setting or two several values, and every combination is replayed in one run. The last days of the period are held out: the value that did best before them is then shown on days that had no say in choosing it."
      >
        <div className="flex flex-col gap-5">
          {axes.map((axis, number) => {
            const setting = settings.find((candidate) => candidate.key === axis.setting)
            const values = setting ? axisValues(axis.values, setting.field, options.limits.sweep_values) : null
            const change = (next: Partial<TypedAxis>) =>
              setAxes((current) => current.map((entry, place) => (place === number ? { ...entry, ...next } : entry)) as [TypedAxis, TypedAxis])
            return (
              <div key={number} className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                <SpringSelect
                  label={number === 0 ? 'Setting' : 'Second setting'}
                  options={[
                    { value: NO_SETTING, label: 'None' },
                    ...settings.map((candidate) => ({ value: candidate.key, label: candidate.label })),
                  ]}
                  value={setting ? axis.setting : NO_SETTING}
                  onChange={(key) => change({ setting: key })}
                />
                {setting && (
                  <Field
                    label={`Values of: ${setting.label}`}
                    value={axis.values}
                    onChange={(typedValues) => change({ values: typedValues })}
                    error={values && 'error' in values && axis.values.trim() !== '' ? values.error : null}
                    help={
                      setting.field.kind === 'boolean'
                        ? 'on, off'
                        : setting.field.choices && setting.field.choices.length > 0
                          ? setting.field.choices.join(', ')
                          : 'Separated by commas: 1, 1.7, 2.5'
                    }
                  />
                )}
              </div>
            )
          })}
          {sweeping && (
            <>
              <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
                <SpringSelect
                  label="Days held out, the last of the period"
                  options={[10, 20, 30, 40, 50]
                    .filter((share) => share >= options.held_out.least * 100 && share <= options.held_out.most * 100)
                    .map((share) => ({ value: String(share), label: `${share}%` }))}
                  value={heldOut}
                  onChange={setHeldOut}
                />
                {leverageValues && leverageValues.length > 1 && (
                  <SpringSelect
                    label="Turbo: index points for 1.00"
                    options={leverageValues.map((leverage) => ({ value: String(leverage), label: String(leverage) }))}
                    value={String(sweepTurbo)}
                    onChange={setSweepLeverage}
                  />
                )}
              </div>
              {sweepError && (
                <p role="alert" className="text-xs text-negative">
                  {sweepError}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                One turbo and no random entries: a sweep is for finding a value. The combination chosen is then made a
                backtest of its own, from the sweep&apos;s page, to be read with all a backtest says.
              </p>
            </>
          )}
        </div>
      </Panel>

      <Panel title="Note">
        <Field
          label="What this run is for (optional)"
          value={note}
          onChange={(value) => setNote(value.slice(0, options.limits.note_length))}
          help="Shown in the list of runs."
        />
      </Panel>

      {createMutation.error && (
        <Alert tone="danger" role="alert">
          <AlertTitle>The backtest was not started</AlertTitle>
          <AlertDescription>{createMutation.error.message}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="submit"
          leftIcon={<Play className="size-4" />}
          disabled={invalid}
          isLoading={createMutation.isPending}
        >
          {sweeping
            ? `Run the sweep${sweepInvalid ? '' : ` of ${combinations} combinations`}`
            : replaying
              ? 'Replay the signals'
              : 'Run the backtest'}
        </Button>
        <p className="text-xs text-muted-foreground">
          {replaying
            ? changedRules.length === 0
              ? 'With the live rules.'
              : `${changedRules.length} rule${changedRules.length === 1 ? '' : 's'} changed.`
            : changedParameters.length + changedRules.length === 0
              ? 'With the strategy’s defaults and the live rules.'
              : `${changedParameters.length} parameter${changedParameters.length === 1 ? '' : 's'} and ${changedRules.length} rule${changedRules.length === 1 ? '' : 's'} changed.`}{' '}
          It runs in the background, one backtest at a time.
        </p>
      </div>
    </form>
  )
}

export function BacktestFormPage() {
  const { backtests_enabled: enabled } = useSession()
  const [search] = useSearchParams()
  const sourceId = search.get('from')

  const optionsQuery = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, enabled })
  const coverageQuery = useQuery({ queryKey: ['backtest-prices'], queryFn: () => api.priceCoverage(), enabled })
  const sourceQuery = useQuery({
    queryKey: ['backtest', sourceId],
    queryFn: sourceId ? () => api.backtest(sourceId) : skipToken,
  })
  const waitingForSource = !!sourceId && sourceQuery.isPending
  const ready = !!optionsQuery.data && !!coverageQuery.data && !waitingForSource

  return (
    <>
      <PageHeader
        title="New backtest"
        description="A strategy, or the signals WATA received, replayed on prices of the past with the rules WATA would have applied."
      />
      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState
          isPending={optionsQuery.isPending || coverageQuery.isPending || waitingForSource}
          error={optionsQuery.error ?? coverageQuery.error}
          hasData={ready}
        >
          {sourceId && sourceQuery.error && (
            <Alert tone="warning">
              <AlertDescription>The run to start from could not be read: the form has the defaults.</AlertDescription>
            </Alert>
          )}
          {optionsQuery.data && coverageQuery.data && (
            // Made once what it starts from is there: its fields take their first values from it.
            <BacktestForm
              key={sourceQuery.data?.id ?? 'new'}
              options={optionsQuery.data}
              coverage={coverageQuery.data}
              source={sourceQuery.data ?? null}
            />
          )}
        </QueryState>
      )}
    </>
  )
}
