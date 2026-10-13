import { keepPreviousData, skipToken, useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import { PageHeader, Panel } from '@/components/Panel'
import { ActionBadge, PnlValue, QueryState } from '@/components/status'
import { useTheme } from '@/components/theme-context'
import { Button } from '@/components/ui/button'
import { useSession } from '@/features/auth/auth-context'
import { ChartAttribution } from '@/features/prices/ChartAttribution'
import { formatChartClock, RANGE } from '@/features/prices/chartData'
import { PriceChart } from '@/features/prices/PriceChart'
import { api } from '@/lib/api'
import { formatDay, formatNumber, formatTime } from '@/lib/format'
import type { BacktestDayChart } from '@/lib/types'
import { cn } from '@/lib/utils'

import { dayView, tradeFocus } from './dayChart'
import type { DayBar } from './dayChart'
import { middleLeverage, runName } from './model'
import { BacktestsDisabled, LeveragePicker } from './parts'
import { TradePathPanel } from './TradePathPanel'

function Legend() {
  return (
    <ul aria-label="Legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="flex">
          <span className="h-0.5 w-2 rounded-l-full" style={{ backgroundColor: 'var(--chart-positive)' }} />
          <span className="h-0.5 w-2 rounded-r-full" style={{ backgroundColor: 'var(--chart-negative)' }} />
        </span>
        Kernel, rising or falling
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="h-0.5 w-4 rounded-full" style={{ backgroundColor: 'var(--chart-series)' }} />
        VWAP of the session
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="w-4 border-t border-dashed" style={{ borderColor: 'var(--chart-series)' }} />
        Its band
      </li>
      <li>
        <span aria-hidden>▲▼</span> Entered
      </li>
      <li>
        <span aria-hidden>●</span> Left (with the result), refused, or went flat
      </li>
    </ul>
  )
}

/** The figures of one range bar: under the pointer, or the last of the day. */
function Readout({ bar, time, decimals, isLast }: { bar: DayBar; time: number; decimals: number; isLast: boolean }) {
  const figure = (value: number | null) => formatNumber(value, decimals)
  const { row } = bar
  return (
    <dl className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-xs" aria-live="off">
      <div className="flex items-baseline gap-1.5">
        <dt className="text-muted-foreground">{isLast ? 'Last bar' : 'Bar'}</dt>
        <dd className="tabular-nums">{formatChartClock(time)}</dd>
      </div>
      <div className="flex items-baseline gap-1.5">
        <dt className="text-muted-foreground">Open, high, low, close</dt>
        <dd className="tabular-nums">
          {figure(row[RANGE.open])} {figure(row[RANGE.high])} {figure(row[RANGE.low])} {figure(row[RANGE.close])}
        </dd>
      </div>
      <div className="flex items-baseline gap-1.5">
        <dt className="text-muted-foreground">Kernel</dt>
        <dd className="tabular-nums">{figure(bar.kernel)}</dd>
      </div>
      <div className="flex items-baseline gap-1.5">
        <dt className="text-muted-foreground">VWAP</dt>
        <dd className="tabular-nums">{figure(bar.vwap)}</dd>
      </div>
      <div className="flex items-baseline gap-1.5">
        <dt className="text-muted-foreground">Band</dt>
        <dd className="tabular-nums">
          {bar.upper !== null && bar.vwap !== null ? `± ${figure(bar.upper - bar.vwap)}` : figure(null)}
        </dd>
      </div>
      <div className="flex items-baseline gap-1.5">
        <dt className="text-muted-foreground">Entries</dt>
        <dd className={cn(!bar.entriesAllowed && 'text-muted-foreground')}>
          {bar.entriesAllowed ? 'allowed' : 'not allowed'}
        </dd>
      </div>
    </dl>
  )
}

interface EventsProps {
  chart: BacktestDayChart
  /** The entry time of the trade being followed, and how to follow another (or none). */
  followed: string | null
  onFollow: (entryTime: string | null) => void
}

function Events({ chart, followed, onFollow }: EventsProps) {
  const { timezone } = useSession()
  if (chart.trades.length + chart.refusals.length + chart.decisions.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">The strategy decided nothing that day.</p>
  }
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="min-w-0">
        <h3 className="mb-2 text-xs font-medium text-muted-foreground">Trades</h3>
        {chart.trades.length === 0 ? (
          <p className="text-sm text-muted-foreground">None taken.</p>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {chart.trades.map((trade) => (
              <li key={trade.entry_time} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="tabular-nums text-muted-foreground">
                  {formatTime(trade.entry_time, timezone)} to {formatTime(trade.exit_time, timezone)}
                </span>
                <ActionBadge action={trade.direction} />
                <span>on {trade.entry_reason}</span>
                <PnlValue value={trade.performance_percent} kind="percent" />
                <span className="text-muted-foreground">{trade.exit_reason}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-pressed={trade.entry_time === followed}
                  onClick={() => onFollow(trade.entry_time === followed ? null : trade.entry_time)}
                >
                  {trade.entry_time === followed ? 'Show the whole day' : 'Follow it'}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="min-w-0">
        <h3 className="mb-2 text-xs font-medium text-muted-foreground">Entries refused</h3>
        {chart.refusals.length === 0 ? (
          <p className="text-sm text-muted-foreground">None.</p>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {chart.refusals.map((refusal, index) => (
              <li key={`${refusal.time}-${index}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="tabular-nums text-muted-foreground">{formatTime(refusal.time, timezone)}</span>
                <ActionBadge action={refusal.direction} />
                <span>{refusal.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export function BacktestDayPage() {
  const { backtests_enabled: enabled } = useSession()
  const { theme } = useTheme()
  const { runId, day } = useParams()
  const [search, setSearch] = useSearchParams()
  const navigate = useNavigate()
  const [hovered, setHovered] = useState<number | null>(null)

  const runQuery = useQuery({
    queryKey: ['backtest', runId],
    queryFn: runId && enabled ? () => api.backtest(runId) : skipToken,
    staleTime: 60_000,
  })
  const run = runQuery.data
  const asked = Number(search.get('leverage'))
  const leverage = run ? (run.turbo.leverages.includes(asked) ? asked : middleLeverage(run.turbo.leverages)) : null

  const chartQuery = useQuery({
    queryKey: ['backtest-day', runId, day, leverage],
    queryFn: runId && day && leverage !== null ? () => api.backtestDayChart(runId, day, leverage) : skipToken,
    staleTime: Infinity,
    // Going to the next day keeps this one on screen, dimmed, until the next arrives.
    placeholderData: keepPreviousData,
  })
  const daysQuery = useQuery({
    queryKey: ['backtest-days', runId, leverage],
    queryFn: runId && leverage !== null ? () => api.backtestDays(runId, leverage) : skipToken,
    staleTime: Infinity,
  })
  const chart = chartQuery.data
  const view = useMemo(() => (chart ? dayView(chart) : null), [chart])
  // One trade of the day followed: brought into view on the chart, and shown as the monitor lived it.
  const followedEntry = search.get('trade')
  const followed = chart?.trades.find((trade) => trade.entry_time === followedEntry) ?? null
  const focus = useMemo(() => (view && followed ? tradeFocus(view, followed) : null), [view, followed])
  const follow = (entryTime: string | null) =>
    setSearch(
      { ...(leverage !== null ? { leverage: String(leverage) } : {}), ...(entryTime ? { trade: entryTime } : {}) },
      { replace: true },
    )

  const days = daysQuery.data?.items.map((entry) => entry.day) ?? []
  const position = day ? days.indexOf(day) : -1
  const goTo = (other: string | undefined) => {
    if (!other || !runId) return
    setHovered(null)
    navigate(`/backtests/${runId}/days/${other}?leverage=${leverage}`)
  }

  const readTime = view ? (hovered !== null && view.barAt.has(hovered) ? hovered : view.lastTime) : null
  const readBar = view && readTime !== null ? view.barAt.get(readTime) : undefined

  return (
    <>
      {runId && (
        <Link to={`/backtests/${runId}`} className="text-xs text-muted-foreground hover:text-foreground">
          ← {run ? runName(run) : 'The backtest'}
        </Link>
      )}
      <PageHeader
        title={day ? formatDay(day) : 'Day'}
        description="The range bars the strategy saw that day, what it measured on them, and what became of its decisions."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              leftIcon={<ChevronLeft className="size-3.5" />}
              disabled={position <= 0}
              onClick={() => goTo(days[position - 1])}
            >
              Day before
            </Button>
            <Button
              variant="outline"
              size="sm"
              rightIcon={<ChevronRight className="size-3.5" />}
              disabled={position < 0 || position >= days.length - 1}
              onClick={() => goTo(days[position + 1])}
            >
              Day after
            </Button>
          </>
        }
      />

      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState
          isPending={runQuery.isPending || chartQuery.isPending}
          error={runQuery.error ?? chartQuery.error}
          hasData={!!run && !!chart && !!view}
        >
          {run && chart && view && leverage !== null && (
            <div className={cn('flex flex-col gap-6 transition-opacity', chartQuery.isPlaceholderData && 'opacity-60')}>
              <LeveragePicker
                leverages={run.turbo.leverages}
                value={leverage}
                onChange={(next) => setSearch({ leverage: String(next) }, { replace: true })}
              />

              <Panel
                title={`Range bars of ${chart.size} points`}
                description={`Times are those of ${chart.timezone}. The trades and refusals are those of a turbo moving by 1.00 for ${chart.leverage} index points.`}
              >
                {view.candles.length === 0 ? (
                  <p className="py-10 text-center text-sm text-muted-foreground">No price that day.</p>
                ) : (
                  <div className="flex flex-col gap-3">
                    {readBar && readTime !== null && (
                      <Readout bar={readBar} time={readTime} decimals={view.decimals} isLast={hovered === null} />
                    )}
                    <div className="max-sm:order-last">
                      <Legend />
                    </div>
                    <PriceChart
                      // The chart draws on a canvas with the colours it read when it was made:
                      // another theme means another chart (only the zoom is lost).
                      key={theme}
                      candles={view.candles}
                      overlays={view.overlays}
                      markers={view.markers}
                      decimals={view.decimals}
                      showSeconds
                      viewKey={view.key}
                      focus={focus}
                      onHover={setHovered}
                      label={`Range bars of the index on ${formatDay(chart.day)}, with the kernel, the VWAP of the session and its band. The trades and refusals are listed below.`}
                      className="h-[30rem]"
                    />
                    <ChartAttribution />
                  </div>
                )}
              </Panel>

              {followed && runId && <TradePathPanel runId={runId} leverage={leverage} trade={followed} />}

              <Panel title="What became of the strategy's decisions">
                <Events chart={chart} followed={followed?.entry_time ?? null} onFollow={follow} />
              </Panel>
            </div>
          )}
        </QueryState>
      )}
    </>
  )
}
