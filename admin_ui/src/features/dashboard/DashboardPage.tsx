import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'

import { KpiTile, Meter } from '@/components/KpiTile'
import { PageHeader, Panel } from '@/components/Panel'
import { ActionBadge, PnlValue, QueryState, ServiceStatusBadge } from '@/components/status'
import { AirportMatrixClock } from '@/components/ui/airport-matrix-clock'
import { Skeleton } from '@/components/ui/skeleton'
import { StatCard } from '@/components/ui/stat-card'
import { useSession } from '@/features/auth/auth-context'
import { ClosePositionButton } from '@/features/positions/ClosePositionButton'
import { LivePositionChart } from '@/features/positions/LivePositionChart'
import { LiveValue } from '@/features/positions/LiveValue'
import { api } from '@/lib/api'
import { formatDateTime, formatDay, formatMoney, formatNumber, formatPercent, formatRelative, toneOf } from '@/lib/format'
import { actionLabel } from '@/lib/labels'
import type { Overview, Position } from '@/lib/types'

import { entryBlockers, openDirections, upcomingNews } from './entryState'
import { cityOfZone, marketClock, NEW_YORK } from './marketClock'
import { PauseControl } from './PauseControl'

/** Re-render on a timer, for the countdowns that depend on the current time. */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

const PNL_TEXT = { positive: 'text-positive', negative: 'text-negative', neutral: undefined }

/**
 * The day's profit and loss, with the days before it that closed a trade as a line to scrub, day by day: the line
 * ends on today as it stands, the figure of the tile.
 */
function DayProfitTile({ overview }: { overview: Overview }) {
  const before = overview.recent_days.filter((day) => day.day_date !== overview.date)
  const closed = overview.day.closed_count
  return (
    <StatCard
      label="Day profit and loss"
      value={overview.day.profit_loss}
      trend={[...before.map((day) => day.profit_loss), overview.day.profit_loss]}
      trendLabels={[...before.map((day) => formatDay(day.day_date)), 'Today']}
      period={`${closed} closed trade${closed === 1 ? '' : 's'}`}
      format={(value) => formatMoney(value, 'EUR', true)}
      toneOf={(value) => PNL_TEXT[toneOf(value)]}
      color="var(--chart-series)"
    />
  )
}

/**
 * New York's time beside the session's, and the US open in the latter: the trading hours are New York's, and the
 * two cities change their clocks on different Sundays.
 */
function MarketClocks({ timeZone, now, className }: { timeZone: string; now: number; className?: string }) {
  const here = cityOfZone(timeZone)
  const cities = useMemo(
    () => [
      { id: 'new-york', name: 'New York', timeZone: NEW_YORK },
      { id: 'here', name: here, timeZone },
    ],
    [here, timeZone],
  )
  const { openHere, hoursApart } = marketClock(now, timeZone)
  return (
    <AirportMatrixClock
      cities={cities}
      size="sm"
      title={null}
      footer={`US OPEN ${openHere} ${here.toUpperCase()} · ${hoursApart} H APART`}
      className={className}
    />
  )
}

function DayPerformanceTile({ overview }: { overview: Overview }) {
  const { performance_percent: performance } = overview.day
  const { profit_target_percent: target, max_day_loss_percent: maxLoss } = overview.limits

  // The bar runs from zero to the limit on the side the day is on, and says so under it.
  let meter = null
  if (performance >= 0 && target) {
    meter = (
      <Meter
        ratio={performance / target}
        tone="positive"
        label="Day performance against the target"
        scale={['0%', `target ${formatPercent(target, true)}`]}
      />
    )
  } else if (performance < 0 && maxLoss) {
    meter = (
      <Meter
        ratio={performance / maxLoss}
        tone="negative"
        label="Day loss against the maximum loss"
        scale={['0%', `stop ${formatPercent(maxLoss, true)}`]}
      />
    )
  }

  return (
    <KpiTile
      label="Day performance"
      value={<PnlValue value={performance} kind="percent" className="font-semibold" />}
      hint="Compounded over the closed trades"
    >
      {meter}
    </KpiTile>
  )
}

function TradesTile({ overview }: { overview: Overview }) {
  const count = overview.day.trade_count
  const limit = overview.limits.max_trades_per_day
  if (limit <= 0) {
    return <KpiTile label="Trades today" value={formatNumber(count)} hint="No daily limit" />
  }
  return (
    <KpiTile
      label="Trades today"
      value={
        <>
          {formatNumber(count)} <span className="text-base font-normal text-muted-foreground">of {limit}</span>
        </>
      }
      hint={count >= limit ? 'Limit reached: no new entry today' : `${limit - count} left`}
    >
      <Meter ratio={count / limit} tone={count >= limit ? 'warning' : 'neutral'} label="Trades against the daily limit" />
    </KpiTile>
  )
}

/**
 * Would a signal that opens a position be traded right now? The answer the
 * pause, the trading hours, the daily limits and the cooldown give together,
 * with the first thing holding it back.
 */
function NextEntryTile({ overview, now }: { overview: Overview; now: number }) {
  const blockers = entryBlockers(overview, now)
  if (blockers.length > 0) {
    return (
      <KpiTile label="Next entry" value={<span className="text-warning">Blocked</span>}>
        {/* Every reason, not the first one only: lifting one must not look like it opens entries. */}
        <ul className="space-y-0.5 text-xs text-muted-foreground">
          {blockers.map((blocker) => (
            <li key={blocker.code}>{blocker.reason}</li>
          ))}
        </ul>
      </KpiTile>
    )
  }

  const duplicates = openDirections(overview).map((direction) => actionLabel(direction).toLowerCase())
  const news = upcomingNews(overview, now)
  const hint =
    duplicates.length > 0
      ? `Except another ${duplicates.join(' or ')} signal: that position is already open`
      : 'Not paused, in trading hours, within the daily limits, no cooldown'
  return <KpiTile label="Next entry" value={<span className="text-positive">Open</span>} hint={news ? `${hint}. ${news}` : hint} />
}

interface OpenPositionsProps {
  positions: Position[]
  timeZone: string
  now: number
  actionsEnabled: boolean
}

function OpenPositions({ positions, timeZone, now, actionsEnabled }: OpenPositionsProps) {
  if (positions.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No open position.</p>
  }
  return (
    <>
      {/* On a phone, what matters first without scrolling sideways: what it is, what it is worth now, the way out. */}
      <ul className="divide-y divide-border/40 text-xs sm:hidden">
        {positions.map((position) => (
          <li key={position.position_id} className="space-y-2 py-3">
            <div className="flex items-start justify-between gap-3">
              <p className="min-w-0 font-medium">{position.instrument_name ?? '—'}</p>
              <ActionBadge action={position.action} />
            </div>
            <div className="flex items-end justify-between gap-3">
              <p className="text-muted-foreground">
                {formatNumber(position.position_amount)} at{' '}
                {formatMoney(position.position_open_price, position.instrument_currency)}
                <br />
                since {formatDateTime(position.execution_time_open, timeZone, false)}
              </p>
              <LiveValue position={position} now={now} />
            </div>
            {actionsEnabled && <ClosePositionButton position={position} />}
          </li>
        ))}
      </ul>
      <OpenPositionsTable positions={positions} timeZone={timeZone} now={now} actionsEnabled={actionsEnabled} />
    </>
  )
}

function OpenPositionsTable({ positions, timeZone, now, actionsEnabled }: OpenPositionsProps) {
  return (
    <div className="hidden overflow-x-auto sm:block">
      <table className="w-full min-w-[40rem] text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">Instrument</th>
            <th className="py-2 pr-4 font-medium">Direction</th>
            <th className="py-2 pr-4 text-right font-medium">Quantity</th>
            <th className="py-2 pr-4 text-right font-medium">Open price</th>
            <th className="py-2 pr-4 font-medium">Opened</th>
            <th className="py-2 pr-4 text-right font-medium">Now</th>
            <th className="py-2 text-right font-medium">Best so far</th>
            {actionsEnabled && <th className="py-2 pl-4" />}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {positions.map((position) => (
            <tr key={position.position_id}>
              <td className="max-w-[18rem] truncate py-2.5 pr-4 font-medium">{position.instrument_name ?? '—'}</td>
              <td className="py-2.5 pr-4">
                <ActionBadge action={position.action} />
              </td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{formatNumber(position.position_amount)}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">
                {formatMoney(position.position_open_price, position.instrument_currency)}
              </td>
              <td className="py-2.5 pr-4">{formatDateTime(position.execution_time_open, timeZone)}</td>
              <td className="py-2.5 pr-4 text-right">
                <LiveValue position={position} now={now} />
              </td>
              <td className="py-2.5 text-right">
                <PnlValue value={position.position_max_performance_percent} kind="percent" />
              </td>
              {actionsEnabled && (
                <td className="py-2.5 pl-4 text-right">
                  <ClosePositionButton position={position} />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function LatestSignals({ timeZone, now }: { timeZone: string; now: number }) {
  const signalsQuery = useQuery({ queryKey: ['signals'], queryFn: api.signals, refetchInterval: 5_000 })
  const signals = signalsQuery.data?.items

  return (
    <QueryState
      isPending={signalsQuery.isPending}
      error={signalsQuery.error}
      hasData={!!signals}
      skeleton={<Skeleton className="h-20 w-full" />}
    >
      {signals && signals.length === 0 && (
        <p className="py-6 text-center text-sm text-muted-foreground">No signal since the web server started.</p>
      )}
      {signals && signals.length > 0 && (
        <ul className="divide-y divide-border/40 text-xs">
          {signals.slice(0, 6).map((signal) => (
            <li key={signal.signal_uuid} className="flex items-center justify-between gap-3 py-2.5">
              <span className="flex min-w-0 items-center gap-2">
                <ActionBadge action={signal.action} />
                <span className="truncate font-medium">{signal.indice}</span>
              </span>
              <span className="shrink-0 text-muted-foreground" title={formatDateTime(signal.received_timestamp, timeZone)}>
                {formatRelative(signal.received_timestamp, now)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </QueryState>
  )
}

function Services() {
  // Same query as the status in the top bar: one request serves both.
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15_000 })
  const health = healthQuery.data

  return (
    <QueryState
      isPending={healthQuery.isPending}
      error={healthQuery.error}
      hasData={!!health}
      skeleton={<Skeleton className="h-32 w-full" />}
    >
      {health && (
        <ul className="divide-y divide-border/40 text-xs">
          {health.services.map((service) => (
            <li key={service.id} className="flex items-center justify-between gap-3 py-2.5">
              <span className="min-w-0">
                <span className="font-medium">{service.name}</span>
                <span className="block truncate text-muted-foreground" title={service.detail}>
                  {service.detail}
                </span>
              </span>
              <ServiceStatusBadge status={service.status} />
            </li>
          ))}
        </ul>
      )}
    </QueryState>
  )
}

export function DashboardPage() {
  const { timezone, actions_enabled: actionsEnabled } = useSession()
  const now = useNow(1_000)
  const overviewQuery = useQuery({ queryKey: ['overview'], queryFn: api.overview, refetchInterval: 5_000 })
  const overview = overviewQuery.data

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={overview ? `${formatDay(overview.date)} · ${overview.timezone}` : 'Today'}
        actions={
          <>
            <MarketClocks timeZone={timezone} now={now} className="hidden sm:block" />
            {overview && overview.entries_paused_since === null && (
              <PauseControl pausedSince={null} timeZone={timezone} actionsEnabled={actionsEnabled} />
            )}
          </>
        }
      />

      <QueryState
        isPending={overviewQuery.isPending}
        error={overviewQuery.error}
        hasData={!!overview}
        skeleton={
          <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className="h-[7.5rem] rounded-2xl" />
            ))}
          </div>
        }
      >
        {overview && (
          <>
            {overview.entries_paused_since !== null && (
              <PauseControl
                pausedSince={overview.entries_paused_since}
                timeZone={timezone}
                actionsEnabled={actionsEnabled}
              />
            )}
            <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
              <DayProfitTile overview={overview} />
              <DayPerformanceTile overview={overview} />
              <TradesTile overview={overview} />
              <NextEntryTile overview={overview} now={now} />
            </div>
            <MarketClocks timeZone={timezone} now={now} className="sm:hidden" />

            <Panel
              title="Open positions"
              description="“Now” is what the position monitor last saw, published every few seconds: performance, unrealised profit or loss, and the bid."
              action={
                <Link to="/positions" className="text-xs text-muted-foreground hover:text-foreground">
                  All positions
                </Link>
              }
              bodyClassName="pt-2"
            >
              <OpenPositions
                positions={overview.open_positions}
                timeZone={timezone}
                now={now}
                actionsEnabled={actionsEnabled}
              />
              {overview.open_positions.length > 0 && (
                <div className="mt-4 grid gap-4 lg:grid-cols-2">
                  {overview.open_positions.map((position) => (
                    <LivePositionChart key={position.position_id} position={position} />
                  ))}
                </div>
              )}
            </Panel>
          </>
        )}
      </QueryState>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Latest signals"
          description="The most recent signal per indice held by the web server."
          action={
            <Link to="/signals" className="text-xs text-muted-foreground hover:text-foreground">
              Signals
            </Link>
          }
          bodyClassName="pt-2"
        >
          <LatestSignals timeZone={timezone} now={now} />
        </Panel>
        <Panel
          title="Services"
          action={
            <Link to="/system" className="text-xs text-muted-foreground hover:text-foreground">
              System
            </Link>
          }
          bodyClassName="pt-2"
        >
          <Services />
        </Panel>
      </div>
    </>
  )
}
