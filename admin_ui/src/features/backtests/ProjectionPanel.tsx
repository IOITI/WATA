import { useQuery } from '@tanstack/react-query'
import { Button, FanChart, Field, formatDay, formatMoney, formatMoneyCompact, formatPercent, KpiStrip, NumberInput, Panel, QueryState, Tabs } from 'ioiti-ui'
import { useMemo, useState } from 'react'

import { api } from '@/lib/api'
import type { BacktestProjection, BacktestRun } from '@/lib/types'

import { sampleWarning } from './model'
import {
  dayOfPlace,
  daysOff,
  daysSentence,
  fanPoints,
  formatIn,
  HORIZON_LABEL,
  HORIZONS,
  inUnit,
  leftOutSentence,
  markers,
  paceSentence,
  shareText,
  simulate,
  startNote,
  tickLabel,
} from './projection'
import type { Horizon, Unit } from './projection'

const percentTick = (value: number) => `${Math.round(value)}%`

/** The run's account at its own pace over the trading days to come, two years at most (src/admin/run_projection.py). */
export function ProjectionPanel({ run }: { run: BacktestRun }) {
  const query = useQuery({
    queryKey: ['backtest-projection', run.id],
    queryFn: () => api.backtestProjection(run.id),
    // The days to come start tomorrow: read again within the hour, not at each visit.
    staleTime: 3_600_000,
  })
  const [unit, setUnit] = useState<Unit>('money')
  return (
    <Panel
      title="At this pace"
      description="The run’s own days drawn again, one for each trading day to come, 2,000 times: weekends, US holidays and early closes, and WATA’s closed dates left out."
      action={
        query.data?.usable && (
          <div className="flex gap-1" role="group" aria-label="Shown in">
            {(['money', 'percent'] as const).map((choice) => (
              <Button key={choice} size="sm" variant={choice === unit ? 'primary' : 'outline'} aria-pressed={choice === unit} onClick={() => setUnit(choice)}>
                {choice === 'money' ? 'Money' : 'Percent'}
              </Button>
            ))}
          </div>
        )
      }
    >
      <QueryState isPending={query.isPending} error={query.error} hasData={!!query.data}>
        {query.data &&
          (query.data.usable ? (
            <Projection projection={query.data} unit={unit} />
          ) : (
            <p className="py-6 text-center text-sm text-muted-foreground">{query.data.why_not}</p>
          ))}
      </QueryState>
    </Panel>
  )
}

function Projection({ projection, unit }: { projection: BacktestProjection; unit: Unit }) {
  const capital = projection.capital ?? 0
  const pace = projection.pace!
  const days = useMemo(() => projection.days ?? [], [projection.days])
  const skipped = projection.skipped ?? []
  const from = projection.from ?? days[0]?.day ?? ''
  const options = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, staleTime: 60_000 })
  const balance = options.data?.capital.balance ?? null

  const [months, setMonths] = useState<Horizon>(12)
  const [asked, setAsked] = useState<number | null>(capital)
  const [cap, setCap] = useState<number | null>(null)
  const start = asked !== null && asked > 0 ? asked : capital
  const fromCapital = asked === capital
  const stakeCap = cap !== null && cap > 0 ? cap : null

  const simulated = useMemo(
    () => simulate({ days, growths: pace.growths, earlyGrowths: pace.early_growths, seed: projection.seed ?? 1 }, start, stakeCap),
    [days, pace, projection.seed, start, stakeCap],
  )
  const horizon = simulated.horizons.find((one) => one.months === months) ?? simulated.horizons[simulated.horizons.length - 1]
  const [p5, p25, , p75, p95] = horizon.levels.map((value) => inUnit(value, unit, start))
  const shown = (value: number) => formatIn(inUnit(value, unit, start), unit)
  const notes = [
    sampleWarning(pace.sample, pace.trades_used, pace.days_used.length),
    leftOutSentence(pace.left_out),
    ...(projection.notes ?? []),
    'The verdict at the top of the page says whether to believe this run: this is what it would make if its days held.',
    'Days drawn one by one know nothing of what they did not see: a market that changes, the depth of the turbos and Saxo’s limits once the stakes grow, taxes, settings chosen on these very days.',
    startNote(start, capital),
    pace.held_past_early_sale > 0
      ? `${pace.held_past_early_sale} of its trades were still held when an early close sells: their morning counts them as they ended.`
      : null,
    projection.calendar?.error ? `The US calendar file could not be read (${projection.calendar.error}): every holiday is estimated by NYSE’s rules.` : null,
    projection.calendar?.config_unreadable.length
      ? `Closed dates of the configuration that cannot be read, left out: ${projection.calendar.config_unreadable.join(', ')}.`
      : null,
  ].filter((note): note is string => Boolean(note))
  const off = daysOff(days, skipped, horizon)

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end gap-4">
        <Field className="w-56" label="Starting with" description={fromCapital ? 'The run’s capital' : undefined}>
          <NumberInput value={asked} onChange={setAsked} min={1} step={100} precision={2} prefix="€" label="Starting with" />
        </Field>
        {balance !== null && balance > 0 && balance !== start && (
          <Button variant="outline" size="sm" onClick={() => setAsked(balance)}>
            Use the account’s balance ({formatMoney(balance, 'EUR')})
          </Button>
        )}
        {!fromCapital && (
          <Button variant="ghost" size="sm" onClick={() => setAsked(capital)}>
            Back to the run’s capital
          </Button>
        )}
        <Field className="w-56" label="Stake at most" description={stakeCap === null ? 'No cap: the stakes grow with the account' : 'Past it, the rest is set aside'}>
          <NumberInput value={cap} onChange={setCap} min={1} step={1000} precision={0} prefix="€" placeholder="No cap" label="Stake at most" />
        </Field>
      </div>

      {/* Held to the panel (a flex item grows to its content): on a phone its tabs scroll. */}
      <Tabs
        className="min-w-0 max-w-full"
        tabs={HORIZONS.map((one) => ({ id: String(one), label: HORIZON_LABEL[one] }))}
        value={String(horizon.months)}
        onChange={(id) => setMonths(Number(id) as Horizon)}
        renderContent={false}
      />

      <KpiStrip
        columns={4}
        items={[
          {
            label: 'At its average day',
            value: shown(horizon.pace),
            hint: `By ${formatDay(horizon.day, true)}, ${horizon.index} trading days on. ${paceSentence(projection) ?? ''}`.trim(),
          },
          { label: 'Half of the paths', value: `${formatIn(p25, unit)} to ${formatIn(p75, unit)}`, hint: `Nine in ten: ${formatIn(p5, unit)} to ${formatIn(p95, unit)}.` },
          {
            label: 'Below the start',
            value: shareText(horizon.belowStart),
            hint: `Below half of it: ${shareText(horizon.belowHalf)}.`,
          },
          {
            label: 'Deepest fall',
            value: formatPercent(-horizon.fallMedian),
            hint: `On the way, for the middle path; one in twenty fell more than ${formatPercent(horizon.fallBad)} from its highest.`,
          },
        ]}
      />

      <FanChart
        aria-label="Where the paths drawn from the run’s days went, over the trading days to come"
        data={fanPoints(simulated, horizon, unit)}
        bands={[
          { lower: 'p5', upper: 'p95', label: 'Nine paths in ten' },
          { lower: 'p25', upper: 'p75', label: 'Half of the paths' },
        ]}
        lines={[{ key: 'pace', label: 'At its average day' }]}
        references={[{ value: unit === 'money' ? start : 0 }]}
        markers={markers(days, horizon, projection.calendar?.estimated_from ?? null)}
        xLabel="Trading days from now"
        formatX={(place) => (place === 0 ? 'Now' : `${formatDay(dayOfPlace(days, from, place), true)}, trading day ${Math.round(place)}`)}
        formatXTick={(place) => tickLabel(dayOfPlace(days, from, place), horizon.months)}
        formatValue={(value) => formatIn(value, unit)}
        formatTick={unit === 'money' ? formatMoneyCompact : percentTick}
        height={280}
      />

      <section className="flex flex-col gap-2">
        <p className="text-sm">{daysSentence(days, skipped, horizon)}</p>
        {off.length > 0 && (
          <ul className="grid gap-x-6 gap-y-1 text-xs text-muted-foreground sm:grid-cols-2" aria-label="Weekdays off and early closes">
            {off.map((day) => (
              <li key={day.day}>
                <span className="tabular-nums text-foreground">{formatDay(day.day, true)}</span> · {day.name} · {day.what}
                {day.estimated ? ' (estimated by NYSE’s rules)' : ''}
              </li>
            ))}
          </ul>
        )}
      </section>

      <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </div>
  )
}
