import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { ReactNode } from 'react'

import { WeekdayHourHeatmap } from '@/components/charts/Heatmaps'
import { CumulativeProfitChart, DailyProfitChart, DistributionChart, ProfitByGroupChart } from '@/components/charts/PerformanceCharts'
import { Panel } from '@/components/Panel'
import { PnlValue, QueryState } from '@/components/status'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { formatNumber, formatPercent } from '@/lib/format'
import type { BacktestBreakdowns, BacktestFigures, BacktestGroupRow, BacktestSummary } from '@/lib/types'

import { curveDays, distribution, FATE_LABEL, RANDOM_VERDICT, randomSentence, randomVerdict, roomSentences, ruleLabel, tally } from './model'
import { Measure } from './parts'
import { riskNote, riskRows, stressSentences } from './robustness'

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="text-xs font-medium text-muted-foreground">{children}</h3>
}

/**
 * What the total of a run is worth: where it stands among entries taken at
 * random on the same recorded turbos, what a trade brings, how much the total
 * owes to the days there were, and what the trades say of a stop and a target.
 */
export function TrustPanel({ runId, summary }: { runId: string; summary: BacktestSummary }) {
  const figures = summary.figures as BacktestFigures
  const found = summary.against_random
  const baselineQuery = useQuery({
    queryKey: ['backtest-baseline', runId],
    queryFn: () => api.backtestBaseline(runId),
    enabled: Boolean(found),
    staleTime: Infinity,
  })
  const totals = baselineQuery.data && 'totals' in baselineQuery.data ? baselineQuery.data.totals : []
  const range = figures.total_range
  const room = roomSentences(figures)
  const risk = figures.risk
  const note = risk ? riskNote(risk, figures.days) : null
  const otherwise = summary.stress ? stressSentences(summary.stress) : []
  return (
    <Panel
      title="How far to trust these figures"
      description="WATA's exits decide much of a trade. A total says something of the entries only where random ones do worse."
    >
      <div className="grid gap-8 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-6">
          <section aria-label="Against random entries">
            <Heading>Against random entries</Heading>
            {found ? (
              <div className="mt-2 flex flex-col gap-3">
                <p>
                  <span className="text-2xl font-semibold tabular-nums">{formatPercent(found.beaten, false, 0)}</span>{' '}
                  <span className="text-sm text-muted-foreground">of the random runs did worse than this one</span>
                </p>
                <p className="text-sm">{RANDOM_VERDICT[randomVerdict(found.beaten)]}</p>
                {totals.length > 0 && (
                  <DistributionChart bins={distribution(totals, figures.total_percent)} counted="Random runs" own={figures.total_percent} ownLabel="This run" />
                )}
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {randomSentence(found)} A random entry is ended by WATA&apos;s rules only: no signal closes it.
                </p>
              </div>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">
                Nothing to compare with: no entry was asked for on the days traded, or the random entries were left out of this run.
              </p>
            )}
          </section>
          {risk && (
            <section aria-label="Risk, day by day">
              <Heading>Risk, day by day</Heading>
              <dl className="mt-2 flex flex-col gap-1.5">
                {riskRows(risk).map((row) => (
                  <Measure key={row.label} label={row.label}>
                    {row.value}
                  </Measure>
                ))}
              </dl>
              {note && <p className="mt-2 text-xs text-muted-foreground">{note}</p>}
            </section>
          )}
        </div>

        <div className="flex min-w-0 flex-col gap-6">
          <section aria-label="Per trade">
            <Heading>Per trade</Heading>
            <dl className="mt-2 flex flex-col gap-1.5">
              <Measure label="On average, wins and losses together">
                <PnlValue value={figures.expectancy} kind="percent" />
              </Measure>
              <Measure label="Average win, average loss">
                {formatPercent(figures.average_win, true)}, {formatPercent(figures.average_loss, true)}
              </Measure>
              <Measure label="Won for 1 lost">
                {figures.profit_factor === null ? (figures.trades > 0 ? 'no loss' : '-') : formatNumber(figures.profit_factor, 2)}
              </Measure>
              <Measure label="Most trades lost in a row">{formatNumber(figures.longest_losing_streak)}</Measure>
              <Measure label="Days won, days lost">
                {formatNumber(figures.winning_days)}, {formatNumber(figures.losing_days)}
              </Measure>
              <Measure label="A day in the middle, and how far days spread">
                {formatPercent(figures.median_day, true)}, ±{formatNumber(figures.day_spread, 2)}
              </Measure>
            </dl>
          </section>

          <section aria-label="The total with other days">
            <Heading>The total with other days of the same kind</Heading>
            {range ? (
              <p className="mt-2 text-sm leading-relaxed">
                Drawing {figures.days} days again from the ones traded, {range.level} totals in 100 fall between{' '}
                <PnlValue value={range.low} kind="percent" /> and <PnlValue value={range.high} kind="percent" />.{' '}
                {formatPercent(range.losing_share, false, 0)} of the draws end in a loss.{' '}
                <span className="text-muted-foreground">It knows nothing of days unlike any of these.</span>
              </p>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">Too few days to draw a range from.</p>
            )}
            {otherwise.map((sentence) => (
              <p key={sentence} className="mt-2 text-sm leading-relaxed">
                {sentence}
              </p>
            ))}
          </section>

          <section aria-label="Room for a stop and a target">
            <Heading>Room for a stop and a target</Heading>
            {room.length > 0 ? (
              <ul className="mt-2 flex flex-col gap-1.5 text-sm leading-relaxed">
                {room.map((sentence) => (
                  <li key={sentence}>{sentence}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">No trade to read it from.</p>
            )}
          </section>
        </div>
      </div>
    </Panel>
  )
}

/** The run's days one after the other, in percent (each trade as the whole stake) or in money. */
export function CurvesPanel({ summary }: { summary: BacktestSummary }) {
  const [unit, setUnit] = useState<'percent' | 'money'>('money')
  const curve = summary.curve ?? []
  const capital = summary.money?.capital ?? 0
  const days = curveDays(curve, unit, capital)
  return (
    <Panel
      title="Day after day"
      description={
        unit === 'percent'
          ? 'Each day as WATA counts it: its trades one after the other, each on the whole stake.'
          : 'The account: what each order bought with what it held, and what each sale gave.'
      }
      action={
        <div className="flex gap-1" role="group" aria-label="Shown in">
          {(['money', 'percent'] as const).map((choice) => (
            <Button key={choice} size="sm" variant={choice === unit ? 'primary' : 'outline'} aria-pressed={choice === unit} onClick={() => setUnit(choice)}>
              {choice === 'money' ? 'Money' : 'Percent'}
            </Button>
          ))}
        </div>
      }
    >
      {days.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No day traded.</p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <CumulativeProfitChart days={days} unit={unit} />
          <DailyProfitChart days={days} unit={unit} />
        </div>
      )}
    </Panel>
  )
}

function Tally({ title, counts, label }: { title: string; counts: Record<string, number> | undefined; label: (name: string) => string }) {
  const rows = tally(counts)
  return (
    <section className="min-w-0">
      <Heading>{title}</Heading>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">None.</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1.5">
          {rows.map((row) => (
            <li key={row.name} className="flex items-center gap-3 text-sm">
              <span className="min-w-0 flex-1 truncate">{label(row.name)}</span>
              <span className="w-24 shrink-0">
                <span className="block h-1.5 rounded-full bg-foreground/15">
                  <span className="block h-1.5 rounded-full bg-foreground/60" style={{ width: `${Math.max(row.share * 100, 2)}%` }} />
                </span>
              </span>
              <span className="w-10 shrink-0 text-right tabular-nums">{row.count}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** What became of the signals, what refused the entries, what made them fail, and what sold the positions. */
export function WhyPanel({ summary }: { summary: BacktestSummary }) {
  const signals = summary.signals
  return (
    <Panel title="Why" description="Every signal of the period and what became of it, as the trader decided; every position and what sold it.">
      <div className="grid gap-8 md:grid-cols-2 xl:grid-cols-4">
        <Tally title="Signals" counts={signals?.by_fate} label={(name) => FATE_LABEL[name as keyof typeof FATE_LABEL] ?? name} />
        <Tally title="Entries refused" counts={signals?.refusals} label={ruleLabel} />
        <Tally title="Entries with no turbo, or failed" counts={signals?.failures} label={ruleLabel} />
        <Tally title="What sold the positions" counts={summary.figures?.exits} label={(name) => name} />
      </div>
    </Panel>
  )
}

type Dimension = Exclude<keyof BacktestBreakdowns['groups'], 'weekday_hour'>

/** The two at once: a heatmap rather than bars and a table. */
const WEEKDAY_HOUR = 'weekday_hour'

const DIMENSIONS: { id: Dimension; label: string; key: string; name?: (value: string | number | null) => string }[] = [
  { id: 'hour', label: 'Hour of entry', key: 'hour', name: (value) => `${String(value).padStart(2, '0')}:00` },
  { id: 'weekday', label: 'Weekday', key: 'weekday' },
  { id: 'direction', label: 'Direction', key: 'direction' },
  { id: 'exit', label: 'Sold on', key: 'exit' },
  { id: 'confidence', label: 'Confidence', key: 'confidence' },
  { id: 'holding', label: 'Held for', key: 'holding' },
  { id: 'turbo_price', label: 'Turbo price', key: 'price' },
  { id: 'issuer', label: 'Issuer', key: 'issuer' },
  { id: 'session_part', label: 'Against the US open', key: 'group' },
  { id: 'volatility', label: 'Day volatility', key: 'group' },
  { id: 'day_type', label: 'Kind of day', key: 'group' },
  { id: 'gap', label: 'Gap', key: 'group' },
  { id: 'us_open', label: 'US open', key: 'group' },
  { id: 'news', label: 'Macro news', key: 'group' },
]

/** The trades of a run by what could explain them, with the figures of the Exits page. */
export function BreakdownsPanel({ runId }: { runId: string }) {
  const [choice, setChoice] = useState<Dimension | typeof WEEKDAY_HOUR>('hour')
  const dimension: Dimension = choice === WEEKDAY_HOUR ? 'hour' : choice
  const breakdownsQuery = useQuery({
    queryKey: ['backtest-breakdowns', runId],
    queryFn: () => api.backtestBreakdowns(runId),
    staleTime: Infinity,
  })
  const breakdowns = breakdownsQuery.data
  const chosen = DIMENSIONS.find((candidate) => candidate.id === dimension) ?? DIMENSIONS[0]
  const groups = (breakdowns?.groups[dimension] ?? []).filter((group) => group.trade_count > 0 || dimension === 'hour')
  const nameOf = (group: BacktestGroupRow) => {
    const value = group[chosen.key] as string | number | null
    return chosen.name ? chosen.name(value) : String(value)
  }
  return (
    <Panel
      title="Where the result comes from"
      description="The trades by the hour and the weekday they were bought (and both at once), their direction, what sold them, their confidence, how long they were held, the turbo's price and its issuer; by their place against the US open and the kind of day they were made on (its volatility among the run's days, its gap, whether it trended, its US open, its macro news)."
    >
      <QueryState isPending={breakdownsQuery.isPending} error={breakdownsQuery.error} hasData={!!breakdowns}>
        {breakdowns && (
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Trades grouped by">
              {DIMENSIONS.filter((candidate) => breakdowns.groups[candidate.id])
                .flatMap((candidate) =>
                  candidate.id === 'weekday' && breakdowns.groups.weekday_hour
                    ? [candidate, { id: WEEKDAY_HOUR, label: 'Weekday and hour' } as const]
                    : [candidate],
                )
                .map((candidate) => (
                  <Button
                    key={candidate.id}
                    variant={candidate.id === choice ? 'primary' : 'outline'}
                    size="sm"
                    aria-pressed={candidate.id === choice}
                    onClick={() => setChoice(candidate.id)}
                  >
                    {candidate.label}
                  </Button>
                ))}
            </div>
            {choice === WEEKDAY_HOUR ? (
              <WeekdayHourHeatmap
                rows={breakdowns.groups.weekday_hour ?? []}
                label="Profit and loss of the run's trades by weekday and hour of entry"
              />
            ) : groups.every((group) => group.trade_count === 0) ? (
              <p className="py-6 text-center text-sm text-muted-foreground">No trade to group.</p>
            ) : (
              <>
                <ProfitByGroupChart
                  groups={groups.map((group) => ({ ...group, profit_loss: group.profit_loss, trade_count: group.trade_count, win_rate: group.win_rate }))}
                  label={nameOf}
                  unit="money"
                  more={(group) => [{ label: 'Per trade', value: formatPercent(group.average_percent, true) }]}
                />
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-muted-foreground">
                      <tr>
                        <th scope="col" className="py-2 pr-4 font-medium">{chosen.label}</th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">Trades</th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">Winning</th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">Per trade</th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">Best level, on average</th>
                        <th scope="col" className="py-2 text-right font-medium">In money</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/40">
                      {groups.map((group) => (
                        <tr key={nameOf(group)}>
                          <th scope="row" className="py-2.5 pr-4 font-normal">{nameOf(group)}</th>
                          <td className="py-2.5 pr-4 text-right tabular-nums">{formatNumber(group.trade_count)}</td>
                          <td className="py-2.5 pr-4 text-right tabular-nums">{formatPercent(group.win_rate, false, 0)}</td>
                          <td className="py-2.5 pr-4 text-right">
                            <PnlValue value={group.average_percent} kind="percent" />
                          </td>
                          <td className="py-2.5 pr-4 text-right tabular-nums">{formatPercent(group.average_best_percent, true)}</td>
                          <td className="py-2.5 text-right">
                            <PnlValue value={group.profit_loss} kind="money" />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-xs text-muted-foreground">
                  Given back from the best level seen to the sale: {formatPercent(breakdowns.given_back.average_given_back, false)} on average;{' '}
                  {breakdowns.given_back.turned_negative_count} trade{breakdowns.given_back.turned_negative_count === 1 ? '' : 's'} (
                  {formatPercent(breakdowns.given_back.turned_negative_percent, false, 0)}) were in profit and closed at a loss.
                </p>
              </>
            )}
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
