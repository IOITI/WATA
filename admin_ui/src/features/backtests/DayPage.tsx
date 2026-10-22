import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useParams, useSearchParams } from 'react-router'

import { ButtonLink } from '@/components/ButtonLink'
import { PageHeader, Panel } from '@/components/Panel'
import { ActionBadge, PnlValue, QueryState } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { useSession } from '@/features/auth/auth-context'
import { ChartAttribution } from '@/features/prices/ChartAttribution'
import { rangeChart, timeChart } from '@/features/prices/chartModel'
import type { ChartModel } from '@/features/prices/chartModel'
import { PriceChart } from '@/features/prices/PriceChart'
import { timeframeOf } from '@/features/ta/engine/timeframes'
import { barsLabel } from '@/features/ta/signals/ruleModel'
import { api } from '@/lib/api'
import { formatDay, formatMoney, formatNumber, formatPercent, formatTime } from '@/lib/format'
import type { BacktestChart, BacktestRun, BacktestSignal, BacktestTrade } from '@/lib/types'
import { cn } from '@/lib/utils'

import { signalMarkers, tradeFocus, tradeMarkers } from './dayChart'
import { assetLabel, dayStatusLabel, heldFor } from './model'
import { BacktestsDisabled, Measure } from './parts'
import { SignalsTable, TradesTable } from './runTables'
import { exitSettings, tradeLevels, tradeOverlays } from './tradeChart'

function assetRef(chart: BacktestChart): string {
  return `${chart.asset.provider}/${chart.asset.key}`
}

/** The chart of the strategy's asset on a day, as the strategy read it: its time bars, or its range bars. */
function useDayChart(chart: BacktestChart | undefined, day: string) {
  return useQuery({
    queryKey: ['backtest-day-chart', chart ? assetRef(chart) : null, chart?.side, chart ? barsLabel(chart.bars) : null, day],
    enabled: Boolean(chart),
    staleTime: 10 * 60_000,
    queryFn: async (): Promise<ChartModel> => {
      const bars = chart!.bars
      const period = { from: day, to: day }
      if (bars.type === 'range') {
        return rangeChart(await api.assetRangeBars(assetRef(chart!), period, { side: chart!.side, size: null, under: null, ticks: bars.ticks }))
      }
      const interval = timeframeOf(bars.timeframe)?.seconds ?? 60
      return timeChart({ prices: await api.assetBars(assetRef(chart!), period, interval), index: null }, chart!.side)
    },
  })
}

/** What sold a trade, in a few words. */
function soldBy(trade: BacktestTrade, closer: BacktestSignal | undefined): string {
  if (trade.exit.kind === 'knocked_out') return 'the knock-out: its quotes stopped'
  const signal = !closer
    ? null
    : closer.source === 'schedule'
      ? `#${closer.id}, the close of the day`
      : closer.action === 'long' || closer.action === 'short'
        ? `#${closer.id}, a ${closer.action} entry`
        : `#${closer.id}, ${closer.action ?? 'a close'}`
  if (trade.exit.kind === 'no_quote') return `${signal ?? 'the end of the day'}, at its last bid: it had no quote left`
  if (trade.exit.kind !== 'explicit') return 'the position monitor'
  return signal ?? 'a signal'
}

/** One trade on the chart of the turbo it bought, with the levels that could have sold it, and all it was made of. */
function TradeDetail({ run, trade, day, signals }: { run: BacktestRun; trade: BacktestTrade; day: string; signals: BacktestSignal[] }) {
  const { timezone } = useSession()
  const settings = useMemo(() => exitSettings(run.settings.settings), [run.settings.settings])
  const turboRef = `saxo/${trade.turbo.instrument_id}`
  const linked = trade.turbo.instrument_id > 0
  const chartQuery = useQuery({
    queryKey: ['backtest-trade-chart', turboRef, assetRef(run.request.chart), day],
    enabled: linked,
    staleTime: 10 * 60_000,
    queryFn: async () => {
      const period = { from: day, to: day }
      const [prices, index] = await Promise.all([
        api.assetBars(turboRef, period, 10),
        api.assetBars(assetRef(run.request.chart), period, 10).catch(() => null),
      ])
      return timeChart({ prices, index }, 'bid')
    },
  })
  const chart = chartQuery.data
  const overlays = useMemo(() => (chart?.kind === 'time' ? tradeOverlays(trade, chart.bars, settings, chart.timeZone) : []), [chart, trade, settings])
  const markers = useMemo(() => (chart ? tradeMarkers([trade], chart.locate, true) : []), [chart, trade])
  const focus = chart ? tradeFocus(trade, chart.locate, 300) : null
  const levels = tradeLevels(trade, settings)
  const opener = signals.find((signal) => signal.id === trade.signal_id)
  const closer = signals.find((signal) => signal.id === trade.exit.signal_id)
  const candidates = trade.entry.candidates

  return (
    <Panel
      title={`Trade #${trade.id}: ${trade.direction} on ${trade.turbo.symbol ?? trade.turbo.instrument_id}`}
      description={trade.turbo.description ?? undefined}
    >
      <div className="flex flex-col gap-6">
        <div className="grid gap-6 lg:grid-cols-3">
          <dl className="flex flex-col gap-1.5">
            <Measure label="Turbo">
              {trade.turbo.symbol} · {trade.turbo.issuer}
            </Measure>
            <Measure label="Knock-out level">{formatNumber(trade.turbo.barrier)}</Measure>
            <Measure label="Bought">
              {formatTime(trade.entry.filled_at, timezone)}, {formatNumber(trade.units)} at {trade.entry.price}
            </Measure>
            <Measure label="Cost">{formatMoney(trade.cost, 'EUR')}</Measure>
            <Measure label="Sold">
              {formatTime(trade.exit.filled_at, timezone)} at {trade.exit.bid}
            </Measure>
            <Measure label="Held">{heldFor(trade.holding_seconds)}</Measure>
          </dl>
          <dl className="flex flex-col gap-1.5">
            <Measure label="Result">
              <PnlValue value={trade.performance_percent} kind="percent" />
            </Measure>
            <Measure label="In money">
              <PnlValue value={trade.profit_loss} kind="money" />
            </Measure>
            <Measure label="Best, worst">
              {formatPercent(trade.max_performance_percent, true)}, {formatPercent(trade.min_performance_percent, true)}
            </Measure>
            <Measure label="Sold on">
              <span title={trade.exit.reason}>{trade.exit.group}</span>
            </Measure>
            <Measure label="Size">
              {formatPercent(trade.scale_percent, false, 1)} of what an order may use
              {trade.confidence !== null ? `, confidence ${trade.confidence}` : ''}
            </Measure>
            <Measure label="The day before it">{formatPercent(trade.day_percent_before, true)}</Measure>
          </dl>
          <dl className="flex flex-col gap-1.5">
            {levels.map((level) => (
              <Measure key={level.id} label={level.label}>
                {level.price.toFixed(3)}
              </Measure>
            ))}
            {settings.trailing_enabled && (
              <Measure label="Trailing stop">
                armed at +{settings.trailing_activation_percent} %, sells {settings.trailing_drawdown_percent} points under the best
              </Measure>
            )}
            <Measure label="Asset at entry, at exit">
              {trade.entry.asset_price ?? '-'}, {trade.exit.asset_price ?? '-'}
            </Measure>
          </dl>
        </div>

        {trade.notes.length > 0 && (
          <Alert tone="warning">
            <AlertDescription>
              <ul className="list-disc pl-4">
                {trade.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        {linked ? (
          <QueryState isPending={chartQuery.isPending} error={chartQuery.error} hasData={!!chart}>
            {chart && (
              <div className="flex flex-col gap-2">
                <PriceChart
                  candles={chart.candles}
                  markers={markers}
                  decimals={Math.max(chart.decimals, 2)}
                  indexLine={chart.indexLine}
                  indexDecimals={chart.indexDecimals}
                  overlays={overlays}
                  showSeconds
                  viewKey={`${chart.key}|${trade.id}`}
                  focus={focus}
                  onHover={() => undefined}
                  label={`The bid of ${trade.turbo.symbol ?? 'the turbo'}, with ${assetLabel(run.request.chart.asset)} below`}
                  className="h-[26rem]"
                />
                <p className="text-xs text-muted-foreground">
                  Its bid every ten seconds as the price recorder kept it. The solid line is the price paid, the dashed ones the
                  stop-loss and the day&apos;s target (and the take-profit when it is near)
                  {settings.trailing_enabled ? ', the one that climbs in steps the trailing stop' : ''}. Below, the strategy&apos;s asset.
                </p>
                <ChartAttribution />
              </div>
            )}
          </QueryState>
        ) : (
          <p className="text-sm text-muted-foreground">
            {run.turbos === 'modelled'
              ? 'A turbo made up from the index: no recorded price of it to draw. Its price was the index’s distance to its level, which the day’s chart shows.'
              : 'This turbo came from files without the recorder’s ids: no chart of it here.'}
          </p>
        )}

        <div className="grid gap-6 lg:grid-cols-2">
          <section className="min-w-0 space-y-2">
            <h3 className="text-xs font-medium text-muted-foreground">The turbos the trader looked at</h3>
            {candidates.length === 0 ? (
              <p className="text-sm text-muted-foreground">-</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th scope="col" className="py-1.5 pr-3 font-medium">Turbo</th>
                      <th scope="col" className="py-1.5 pr-3 text-right font-medium">Knock-out</th>
                      <th scope="col" className="py-1.5 pr-3 text-right font-medium">Bid</th>
                      <th scope="col" className="py-1.5 text-right font-medium">Ask</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {candidates.map((candidate) => (
                      <tr key={candidate.instrument_id} className={cn(candidate.chosen && 'font-semibold', !candidate.in_range && 'text-muted-foreground')}>
                        <td className="py-1.5 pr-3">
                          {candidate.symbol ?? candidate.instrument_id} {candidate.issuer}
                          {candidate.chosen && <Badge tone="success" className="ml-2">bought</Badge>}
                        </td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{formatNumber(candidate.barrier)}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{candidate.bid ?? '-'}</td>
                        <td className="py-1.5 text-right tabular-nums">{candidate.ask ?? '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              The twenty closest to their knock-out level that were recorded and alive then; the lowest bid in the price range was bought.
            </p>
          </section>
          <section className="min-w-0 space-y-2">
            <h3 className="text-xs font-medium text-muted-foreground">The signals</h3>
            <dl className="flex flex-col gap-1.5">
              <Measure label="Bought on">
                {opener ? (
                  <>
                    #{opener.id} <ActionBadge action={opener.action} /> {formatTime(opener.bar_closed_at ?? opener.arrived_at, timezone)}
                    {opener.alert?.title ? ` (${opener.alert.title})` : ''}
                  </>
                ) : (
                  '-'
                )}
              </Measure>
              <Measure label="Decided, filled">
                {formatTime(trade.entry.decided_at, timezone)}, {formatTime(trade.entry.filled_at, timezone)}
              </Measure>
              <Measure label="Sold by">{soldBy(trade, closer)}</Measure>
            </dl>
          </section>
        </div>
      </div>
    </Panel>
  )
}

export function DayPage() {
  const { runId = '', day = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const { timezone, backtests_enabled: enabled } = useSession()
  const tradeId = Number(params.get('trade')) || null

  const runQuery = useQuery({ queryKey: ['backtest', runId], queryFn: () => api.backtest(runId), enabled })
  const run = runQuery.data
  const done = run?.status.state === 'done'
  const daysQuery = useQuery({ queryKey: ['backtest-days', runId], queryFn: () => api.backtestDays(runId), enabled: done, staleTime: Infinity })
  const tradesQuery = useQuery({
    queryKey: ['backtest-trades', runId, day],
    queryFn: () => api.backtestTrades(runId, { day }),
    enabled: done,
    staleTime: Infinity,
  })
  const signalsQuery = useQuery({
    queryKey: ['backtest-signals', runId, day],
    queryFn: () => api.backtestSignals(runId, { day }),
    enabled: done,
    staleTime: Infinity,
  })
  const chartQuery = useDayChart(run?.request.chart, day)
  const trades = useMemo(() => tradesQuery.data?.items ?? [], [tradesQuery.data])
  const signals = useMemo(() => signalsQuery.data?.items ?? [], [signalsQuery.data])
  const chart = chartQuery.data
  const selected = trades.find((trade) => trade.id === tradeId) ?? null
  const markers = useMemo(
    () => (chart ? [...signalMarkers(signals, chart.locate), ...tradeMarkers(trades, chart.locate)].sort((first, second) => first.time - second.time) : []),
    [chart, signals, trades],
  )
  const focus = chart && selected ? tradeFocus(selected, chart.locate) : null

  const days = daysQuery.data?.items ?? []
  const record = days.find((entry) => entry.day === day)
  const traded = days.filter((entry) => entry.status === 'tested').map((entry) => entry.day)
  const place = traded.indexOf(day)
  const previous = place > 0 ? traded[place - 1] : traded.filter((entry) => entry < day).pop()
  const next = place >= 0 ? traded[place + 1] : traded.find((entry) => entry > day)
  // A trade clicked in a table below is opened above them: brought into view.
  const detail = useRef<HTMLDivElement>(null)
  const bringIntoView = useRef(false)
  const show = useCallback(
    (trade: BacktestTrade | number | null) => {
      const id = typeof trade === 'number' ? trade : trade?.id
      bringIntoView.current = Boolean(id)
      setParams((current) => {
        const changed = new URLSearchParams(current)
        if (id) changed.set('trade', String(id))
        else changed.delete('trade')
        return changed
      })
    },
    [setParams],
  )
  useEffect(() => {
    if (selected && bringIntoView.current) {
      bringIntoView.current = false
      detail.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    }
  }, [selected])

  return (
    <>
      <PageHeader
        title={formatDay(day)}
        description={run ? `${run.name}: every signal of the day and what became of it, every trade and its turbo.` : undefined}
        parents={run ? [{ label: run.name, href: `/backtests/${runId}` }] : []}
        actions={
          <>
            <ButtonLink to={previous && `/backtests/${runId}/days/${previous}`} variant="outline" size="sm" leftIcon={<ChevronLeft className="size-3.5" />}>
              Day before
            </ButtonLink>
            <ButtonLink to={next && `/backtests/${runId}/days/${next}`} variant="outline" size="sm" leftIcon={<ChevronRight className="size-3.5" />}>
              Day after
            </ButtonLink>
          </>
        }
      />
      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState isPending={runQuery.isPending} error={runQuery.error} hasData={!!run}>
          {run && (
            <div className="flex flex-col gap-6">
              {record && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                  <Badge tone={record.status === 'tested' ? 'success' : 'neutral'}>{dayStatusLabel(record.status, run.turbos)}</Badge>
                  <span>
                    Day <PnlValue value={record.day_percent} kind="percent" />, <PnlValue value={record.profit_loss} kind="money" />
                  </span>
                  <span className="text-muted-foreground">
                    {record.trades} trade{record.trades === 1 ? '' : 's'},{' '}
                    {Object.values(record.signals).reduce((sum: number, count) => sum + (count ?? 0), 0)} signals
                    {record.recorded ? `, ${record.recorded.long} long and ${record.recorded.short} short turbos recorded` : ''}
                  </span>
                </div>
              )}

              <Panel
                title={`${assetLabel(run.request.chart.asset)} · ${barsLabel(run.request.chart.bars)}`}
                description="The asset the strategy read, as it read it. Arrows: the signals of entry (✕ when nothing was bought) and the purchases (#); dots: the sales, with what each made."
              >
                <QueryState isPending={chartQuery.isPending} error={chartQuery.error} hasData={!!chart}>
                  {chart && (
                    <div className="flex flex-col gap-2">
                      {chart.hasCandles ? (
                        <PriceChart
                          candles={chart.candles}
                          markers={markers}
                          decimals={chart.decimals}
                          showSeconds={chart.showSeconds}
                          viewKey={`${chart.key}|${selected?.id ?? 'day'}`}
                          focus={focus}
                          onHover={() => undefined}
                          label={`${assetLabel(run.request.chart.asset)} on ${formatDay(day)}`}
                          className="h-[28rem]"
                        />
                      ) : (
                        <p className="py-6 text-center text-sm text-muted-foreground">No bar of the asset that day.</p>
                      )}
                      <ChartAttribution />
                    </div>
                  )}
                </QueryState>
              </Panel>

              {selected && (
                <div ref={detail} className="scroll-mt-20">
                  <TradeDetail run={run} trade={selected} day={day} signals={signals} />
                </div>
              )}

              <Panel title="Trades of the day" description="A click on a trade shows it on the chart and opens it on its turbo's.">
                <QueryState isPending={tradesQuery.isPending} error={tradesQuery.error} hasData={!!tradesQuery.data}>
                  <TradesTable runId={runId} trades={trades} timezone={timezone} onShow={(trade) => show(trade.id === selected?.id ? null : trade)} withDay={false} />
                </QueryState>
              </Panel>

              <Panel title="Signals of the day">
                <QueryState isPending={signalsQuery.isPending} error={signalsQuery.error} hasData={!!signalsQuery.data}>
                  <SignalsTable runId={runId} signals={signals} timezone={timezone} withDay={false} onTrade={(id) => show(id)} />
                </QueryState>
              </Panel>
            </div>
          )}
        </QueryState>
      )}
    </>
  )
}
