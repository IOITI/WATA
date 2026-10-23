// The parts of the Engine monitor that read a period: the rules' runs, the charts over time, the
// delivery of the live signals and their results.

import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { CountBarsChart, SeriesLegend, TimeSeriesChart } from '@/components/charts/PerformanceCharts'
import type { SeriesLine } from '@/components/charts/PerformanceCharts'
import { Meter } from '@/components/KpiTile'
import { Panel } from '@/components/Panel'
import { ClaudeBadge, PnlValue } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Badge } from '@/components/ui/badge'
import { madeByClaude } from '@/features/system/claude'
import { formatDay, formatNumber, formatPercent, formatWait } from '@/lib/format'
import type { TaMonitor, TaMonitorCount, TaMonitorRule, TaMonitorStep } from '@/lib/types'

import { barsLabel, MODE_LABEL, MODE_TONE } from '../signals/ruleModel'
import {
  axisClock,
  axisTicks,
  barLength,
  bucketTitle,
  checksLabel,
  coarserCounts,
  continuedLabel,
  differedLabel,
  filledBuckets,
  limitInView,
  limitText,
  memoryLines,
  monitorHref,
  ruleName,
  stepShares,
  toneOf,
} from './monitorModel'
import type { ResultRow } from './monitorModel'

const MEDIAN = 'var(--chart-muted)'
const HIGH = 'var(--chart-series)'
// Each line of memory keeps its colour whichever of them have figures.
const MEMORY_COLORS: Record<string, string> = {
  memory_used_mb: 'var(--chart-compare-1)',
  live_rss_mb: 'var(--chart-compare-2)',
  shadow_rss_mb: 'var(--chart-compare-3)',
  engine_rss_mb: 'var(--chart-compare-4)',
}
// The run charts measure runs: a bar of time in which no rule had a new bar (on range bars, a quiet price) has none.
const NO_NEW_BAR = 'Where the line breaks, no rule had a new bar to run: nothing was measured then.'
const SIGNAL_BARS: SeriesLine[] = [
  { key: 'sent', label: 'Sent', color: 'var(--chart-positive)' },
  { key: 'not_sent', label: 'Not sent', color: 'var(--chart-negative)' },
  { key: 'shadow', label: 'In shadow', color: 'var(--chart-muted)' },
]

function megabytes(value: number): string {
  return `${formatNumber(value)} MB`
}

function spread(values: { p50: number | null; p95: number | null }): string {
  return values.p50 === null ? '—' : `${formatWait(values.p50)} · ${formatWait(values.p95 ?? values.p50)}`
}

// ── the rules ──

function Differed({ differed }: { differed: { summary: string; detail: string } | null }) {
  if (!differed) return null
  return (
    <details className="mt-1 text-xs">
      <summary className="w-fit cursor-pointer select-none text-warning">{differed.summary}</summary>
      <p className="mt-1 break-words text-muted-foreground">{differed.detail}</p>
    </details>
  )
}

export function RulesPanel({ period }: { period: TaMonitor }) {
  const { limits } = period
  const columns = useMemo<ColumnDef<TaMonitorRule>[]>(
    () => [
      {
        id: 'rule',
        header: 'Rule',
        accessorFn: (row) => ruleName(row),
        sortable: true,
        cell: ({ row }) => (
          <div className="min-w-0 max-w-72 whitespace-normal">
            <Link className="font-medium underline-offset-2 hover:underline" to={monitorHref({ hours: period.hours, rule: row.rule_id })}>
              {ruleName(row)}
            </Link>
            {madeByClaude(row) && <ClaudeBadge className="ml-2" />}
            <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              {row.mode && <Badge tone={MODE_TONE[row.mode]}>{MODE_LABEL[row.mode]}</Badge>}
              {row.bars && <span>{barsLabel(row.bars)}</span>}
            </p>
            <Differed differed={differedLabel(row)} />
          </div>
        ),
      },
      {
        id: 'runs',
        header: 'Runs',
        accessorKey: 'runs',
        sortable: true,
        align: 'right',
        cell: ({ row }) => (
          <div className="whitespace-nowrap text-right tabular-nums">
            {formatNumber(row.runs)}
            <p className="text-xs text-muted-foreground">
              {formatNumber(row.loads)} load{row.loads === 1 ? '' : 's'}
              {row.asks > 0 && `, ${formatNumber(row.asks)} series asked`}
            </p>
            {continuedLabel(row) && <p className="text-xs text-muted-foreground">{continuedLabel(row)}</p>}
          </div>
        ),
      },
      {
        id: 'run',
        header: 'Run time',
        accessorFn: (row) => row.run_ms.max ?? -1,
        sortable: true,
        cell: ({ row }) => (
          // Its meter's width in the table; on a phone's card, the half of it a field has.
          <div className="w-44 max-w-full whitespace-normal">
            <p className="tabular-nums">{spread(row.run_ms)}</p>
            <p className="text-xs text-muted-foreground">
              median · 95 %{row.run_ms.max !== null && `; slowest ${formatWait(row.run_ms.max)}`}
            </p>
            {row.run_ms.max !== null && (
              <div className="mt-1.5">
                <Meter
                  ratio={row.run_ms.max / row.limit_ms}
                  tone={toneOf(row.run_ms.max, row.limit_ms, limits.share)}
                  label={`The slowest run of ${ruleName(row)} against its limit`}
                  scale={['0', limitText(row.limit_ms)]}
                />
              </div>
            )}
            {row.pinets_ms_avg !== null && (
              <p className="mt-1 text-xs text-muted-foreground">PineTS {formatWait(row.pinets_ms_avg)} of a run, on average</p>
            )}
            {checksLabel(row) && <p className="mt-1 text-xs text-muted-foreground">{checksLabel(row)}</p>}
          </div>
        ),
      },
      {
        id: 'delay',
        header: 'Bar close → done',
        accessorFn: (row) => row.delay_ms.max ?? -1,
        sortable: true,
        cell: ({ row }) => (
          <div className="whitespace-nowrap">
            <p className="tabular-nums">{spread(row.delay_ms)}</p>
            {row.delay_ms.max !== null && <p className="text-xs text-muted-foreground">slowest {formatWait(row.delay_ms.max)}</p>}
          </div>
        ),
      },
      {
        id: 'failures',
        header: 'Failed',
        accessorFn: (row) => row.failed + row.ended,
        sortable: true,
        cell: ({ row }) =>
          row.failed + row.ended === 0 ? (
            <span className="text-xs text-muted-foreground">none</span>
          ) : (
            <div className="min-w-0 max-w-64 whitespace-normal text-xs">
              <span className="font-medium text-negative">
                {row.failed > 0 && `${formatNumber(row.failed)} by its script`}
                {row.failed > 0 && row.ended > 0 && ', '}
                {row.ended > 0 && `${formatNumber(row.ended)} with its runner`}
              </span>
              {row.last_failure && <p className="mt-0.5 text-muted-foreground">{row.last_failure}</p>}
            </div>
          ),
      },
      {
        id: 'deferred',
        header: 'Waited',
        accessorKey: 'deferred',
        sortable: true,
        align: 'right',
        cell: ({ row }) => (
          <span className="tabular-nums" title="Ticks it waited for the shadow rules' share of a tick">
            {row.deferred > 0 ? `${formatNumber(row.deferred)} ticks` : '—'}
          </span>
        ),
      },
      {
        id: 'signals',
        header: 'Signals',
        accessorFn: (row) => row.signals.raised,
        sortable: true,
        cell: ({ row }) => (
          <div className="whitespace-nowrap text-xs">
            <span className="text-sm font-medium tabular-nums">{formatNumber(row.signals.raised)}</span>
            <p className="text-muted-foreground">
              {row.mode === 'shadow' && row.signals.sent === 0
                ? 'journaled in shadow'
                : `${formatNumber(row.signals.sent)} sent, ${formatNumber(row.signals.not_sent)} not`}
            </p>
          </div>
        ),
      },
    ],
    [period.hours, limits.share],
  )
  return (
    <Panel
      id="rules"
      title="Rules"
      description={`Each rule's runs in the period: the runner's time (median and 95 %), against its limit, the runner's ${limitText(limits.run_timeout_ms)} or its bar when shorter; and from its bar's close to its signals journaled and sent.`}
    >
      <AdvancedDataTable<TaMonitorRule>
        searchPlaceholder="Search…"
        emptyTitle="No rule measured in this period"
        emptyDescription="A rule switched on is measured from the engine's next tick."
        data={period.rules}
        columns={columns}
        getRowId={(row) => row.rule_id}
        defaultPageSize={20}
        selectable={false}
      />
    </Panel>
  )
}

// ── over time ──

function ChartCard({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <figure className="min-w-0">
      <figcaption>
        <h3 className="text-sm font-medium">{title}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
      </figcaption>
      <div className="mt-3">{children}</div>
    </figure>
  )
}

export function ChartsPanel({ period, timeZone }: { period: TaMonitor; timeZone: string }) {
  const { limits, series } = period
  const clock = axisClock(period.hours, timeZone)
  const title = bucketTitle(period.bucket_seconds, timeZone)
  const marks = series.marks.map((mark) => ({ t: mark.t, label: mark.label }))
  const gaps = <T extends { t: number }>(points: readonly T[]) =>
    filledBuckets(points as unknown as ({ t: number } & Record<string, number | null>)[], period.start, period.end, period.bucket_seconds, (t) => ({ t }))
  const runs = gaps(series.runs)
  const ticks = gaps(series.ticks)
  const ticksAt = axisTicks(period.start, period.end, period.hours, timeZone)
  // Bars of counts long enough to be seen: those of the period's charts would be a pixel wide.
  const counts = coarserCounts(
    filledBuckets(series.signals, period.start, period.end, period.bucket_seconds, (t) => ({ t, sent: 0, not_sent: 0, shadow: 0 })),
    ['sent', 'not_sent', 'shadow'] as const,
    period.bucket_seconds,
    timeZone,
  )
  const countStarts = new Set(counts.points.map((point) => point.t))
  const chosen = period.rule ? period.rules.find((rule) => rule.rule_id === period.rule) : undefined
  const runLimit = chosen?.limit_ms ?? limits.run_timeout_ms
  const memory = memoryLines(series.ticks).map((line) => ({ ...line, color: MEMORY_COLORS[line.key] }))
  const memoryLimit = series.ticks.find((point) => point.memory_limit_mb !== null)?.memory_limit_mb ?? null
  const runLines: SeriesLine[] = [
    { key: 'run_p50_ms', label: 'Median', color: MEDIAN },
    { key: 'run_p95_ms', label: '95 %', color: HIGH },
    { key: 'run_max_ms', label: 'Slowest', color: HIGH, dashed: true },
  ]
  const delayLines: SeriesLine[] = [
    { key: 'delay_p50_ms', label: 'Median', color: MEDIAN },
    { key: 'delay_max_ms', label: 'Slowest', color: HIGH },
  ]
  const tickLines: SeriesLine[] = [
    { key: 'tick_p50_ms', label: 'Median', color: MEDIAN },
    { key: 'tick_max_ms', label: 'Longest', color: HIGH },
  ]
  const feedLines: SeriesLine[] = [
    { key: 'feed_lag_p50_ms', label: 'Median', color: MEDIAN },
    { key: 'feed_lag_max_ms', label: 'Most', color: HIGH },
  ]
  const inView = (values: readonly ({ t: number } & Record<string, unknown>)[], keys: string[], value: number | null, label: string) => {
    if (value === null) return null
    const shown = limitInView(values.flatMap((point) => keys.map((key) => point[key] as number | null | undefined)), value)
    return shown === null ? null : { value: shown, label }
  }
  const restarts = marks.length ? 'Restarts' : undefined
  return (
    <Panel
      id="charts"
      title="Over time"
      description={`In bars of ${barLength(period.bucket_seconds)}. A limit is drawn once the figures come near it; the dotted lines across are the engine's restarts (each day's, a runner started again).`}
    >
      <div className="grid gap-x-8 gap-y-10 lg:grid-cols-2">
        <ChartCard
          title="Run time"
          description={`${chosen ? ruleName(chosen) : 'Every rule'}: the runner's time over each new bar; its limit ${limitText(runLimit)}. ${NO_NEW_BAR}`}
        >
          <TimeSeriesChart
            points={runs}
            lines={runLines}
            format={formatWait}
            clock={clock}
            title={title}
            marks={marks}
            ticks={ticksAt}
            limit={inView(runs, ['run_max_ms'], runLimit, `Limit ${limitText(runLimit)}`)}
          />
          <SeriesLegend lines={runLines} marks={restarts} />
        </ChartCard>
        <ChartCard
          title="Bar close → signals done"
          description={`How long after a bar closed its run had journaled and sent its signals; a live signal is refused past ${limitText(limits.max_signal_age_ms)}. ${NO_NEW_BAR}`}
        >
          <TimeSeriesChart
            points={runs}
            lines={delayLines}
            format={formatWait}
            clock={clock}
            title={title}
            marks={marks}
            ticks={ticksAt}
            limit={inView(runs, ['delay_max_ms'], limits.max_signal_age_ms, `Refused past ${limitText(limits.max_signal_age_ms)}`)}
          />
          <SeriesLegend lines={delayLines} marks={restarts} />
        </ChartCard>
        <ChartCard
          title="Tick time"
          description={`One tick every ${limitText(limits.tick_ms)} gives each rule its new bars; the longest are the daily restart's, which loads every rule, and those that checked a continued run against a whole one.`}
        >
          <TimeSeriesChart
            points={ticks}
            lines={tickLines}
            format={formatWait}
            clock={clock}
            title={title}
            marks={marks}
            ticks={ticksAt}
            limit={inView(ticks, ['tick_max_ms'], limits.tick_ms, `One every ${limitText(limits.tick_ms)}`)}
          />
          <SeriesLegend lines={tickLines} marks={restarts} />
        </ChartCard>
        <ChartCard
          title="Feed behind"
          description={`How far the Capital.com feed's bars were behind each tick; nothing is sent past ${limitText(limits.stale_feed_ms)}.`}
        >
          <TimeSeriesChart
            points={ticks}
            lines={feedLines}
            format={formatWait}
            clock={clock}
            title={title}
            marks={marks}
            ticks={ticksAt}
            limit={inView(ticks, ['feed_lag_max_ms'], limits.stale_feed_ms, `Stale past ${limitText(limits.stale_feed_ms)}`)}
          />
          <SeriesLegend lines={feedLines} marks={restarts} />
        </ChartCard>
        <ChartCard
          title="Memory"
          description="The most each held in a bar of time: the container (its file cache left out), the runners (Node), the engine (Python)."
        >
          {memory.length === 0 ? (
            <p className="py-16 text-center text-sm text-muted-foreground">Memory is read on the server alone (Linux, cgroup v2).</p>
          ) : (
            <>
              <TimeSeriesChart
                points={ticks}
                lines={memory}
                format={megabytes}
                clock={clock}
                title={title}
                marks={marks}
                ticks={ticksAt}
                yWidth={72}
                limit={inView(ticks, ['memory_used_mb'], memoryLimit, `The container's ${memoryLimit === null ? '' : megabytes(memoryLimit)}`)}
              />
              <SeriesLegend lines={memory} marks={restarts} />
            </>
          )}
        </ChartCard>
        <ChartCard
          title="Signals"
          description={`The signals the rules raised, by what became of them in the engine, in bars of ${barLength(counts.bucket)}.`}
        >
          <CountBarsChart
            points={counts.points}
            bars={SIGNAL_BARS}
            clock={clock}
            title={bucketTitle(counts.bucket, timeZone)}
            ticks={ticksAt.filter((t) => countStarts.has(t))}
          />
          <SeriesLegend lines={SIGNAL_BARS} />
        </ChartCard>
      </div>
    </Panel>
  )
}

// ── the delivery of the live signals ──

function StepRow({ step, share }: { step: TaMonitorStep; share: number | undefined }) {
  return (
    <tr className={step.total ? 'font-medium' : undefined}>
      <td className="py-2 pr-4">{step.label}</td>
      <td className="py-2 pr-4 text-right tabular-nums text-muted-foreground">{formatNumber(step.signals)}</td>
      <td className="py-2 pr-4 text-right tabular-nums">{step.p50 === null ? '—' : formatWait(step.p50)}</td>
      <td className="py-2 pr-4 text-right tabular-nums">{step.p95 === null ? '—' : formatWait(step.p95)}</td>
      <td className="py-2 pr-4 text-right tabular-nums text-muted-foreground">{step.max === null ? '—' : formatWait(step.max)}</td>
      <td className="w-40 py-2">
        {share !== undefined && (
          <div aria-hidden className="h-1.5 rounded-full" style={{ width: `${Math.max(share * 100, 2)}%`, backgroundColor: HIGH }} />
        )}
      </td>
    </tr>
  )
}

function Counts({ title, items }: { title: string; items: (TaMonitorCount & { detail?: string | null })[] }) {
  const shown = items.filter((item) => item.count > 0)
  return (
    <div>
      <h3 className="text-sm font-medium">{title}</h3>
      {shown.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">None in this period.</p>
      ) : (
        <ul className="mt-2 space-y-1.5 text-sm">
          {shown.map((item) => (
            <li key={`${item.key}-${item.detail ?? ''}`} className="flex items-baseline justify-between gap-4">
              <span className="min-w-0">
                {item.label}
                {item.detail && <span className="block text-xs text-muted-foreground">{item.detail}</span>}
              </span>
              <span className="tabular-nums">{formatNumber(item.count)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function DeliveryPanel({ period }: { period: TaMonitor }) {
  const { delivery } = period
  const shares = stepShares(delivery.steps)
  const steps = delivery.steps.filter((step) => step.signals > 0)
  return (
    <Panel
      id="delivery"
      title="Delivery of the live signals"
      description="From a bar's close to its position, each step between two moments the journals keep, all on the server's clock: the engine's (its tick, its run, the web server's answer), then the trader's (its order, Saxo's answer, the position)."
    >
      {steps.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No live signal was sent in this period.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[44rem] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">Step</th>
                <th className="py-2 pr-4 text-right font-medium">Signals</th>
                <th className="py-2 pr-4 text-right font-medium">Median</th>
                <th className="py-2 pr-4 text-right font-medium">95 %</th>
                <th className="py-2 pr-4 text-right font-medium">Slowest</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">Median against the longest step</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {steps.map((step) => (
                <StepRow key={step.step} step={step} share={shares.get(step.step)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-6 grid gap-6 md:grid-cols-2">
        <Counts
          title="What the engine did with the signals"
          items={[
            ...delivery.engine.filter((item) => item.key !== 'not_sent'),
            ...delivery.refusals.map((refusal) => ({ ...refusal, label: `Not sent: ${refusal.label.toLowerCase()}`, detail: refusal.example })),
          ]}
        />
        <Counts
          title="What the trader made of those sent"
          items={delivery.trader.map((outcome) => ({ ...outcome, detail: outcome.reason }))}
        />
      </div>
      {delivery.shadow_late > 0 && (
        <p className="mt-4 text-xs text-muted-foreground">
          {formatNumber(delivery.shadow_late)} signal{delivery.shadow_late === 1 ? '' : 's'} of the shadow rules came too late to be sent,
          had {delivery.shadow_late === 1 ? 'its rule' : 'their rules'} been live.
        </p>
      )}
    </Panel>
  )
}

// ── results ──

export function ResultsPanel({ rows, backtestsEnabled }: { rows: ResultRow[]; backtestsEnabled: boolean }) {
  return (
    <Panel
      id="results"
      title="Results"
      description="What the rules' signals made in the period: the positions a live rule's signals opened, and a shadow rule's finished days traded on paper (a backtest of each, once the market is closed)."
    >
      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No position and no day on paper in this period.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">Rule</th>
                <th className="py-2 pr-4 text-right font-medium">Positions</th>
                <th className="py-2 pr-4 text-right font-medium">Won · lost</th>
                <th className="py-2 pr-4 text-right font-medium">Result</th>
                <th className="py-2 font-medium">On paper</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {rows.map((row) => (
                <tr key={row.rule_id}>
                  <td className="py-2.5 pr-4">
                    <span className="font-medium">{ruleName(row)}</span>
                    {madeByClaude(row) && <ClaudeBadge className="ml-2" />}
                    {row.mode && <p className="text-muted-foreground">{MODE_LABEL[row.mode]}</p>}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">
                    {row.live ? (
                      <>
                        {formatNumber(row.live.positions)}
                        {row.live.open > 0 && <p className="text-muted-foreground">{formatNumber(row.live.open)} open</p>}
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">
                    {row.live ? `${formatNumber(row.live.won)} · ${formatNumber(row.live.lost)}` : '—'}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">
                    {row.live?.profit_loss != null ? <PnlValue value={row.live.profit_loss} kind="money" currency="EUR" /> : '—'}
                    {row.live?.average_percent != null && (
                      <p className="text-muted-foreground">{formatPercent(row.live.average_percent, true)} a position</p>
                    )}
                  </td>
                  <td className="py-2.5">
                    {row.paper ? (
                      <>
                        {formatNumber(row.paper.days)} day{row.paper.days === 1 ? '' : 's'}, {formatPercent(row.paper.total, true)}
                        {row.paper.last && (
                          <p>
                            <Link className="text-muted-foreground underline-offset-2 hover:underline" to={`/backtests/${row.paper.last.run_id}`}>
                              Last, {formatDay(row.paper.last.day, false)}: {formatPercent(row.paper.last.percent, true)}
                            </Link>
                          </p>
                        )}
                      </>
                    ) : (
                      <span className="text-muted-foreground">{row.mode === 'shadow' && !backtestsEnabled ? 'Needs the backtests' : '—'}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
