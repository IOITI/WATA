// A day of a signal rule's live check (/ta/signals/<id>/live/<day>): each signal of the day on each track (the backtest
// linked to the rule, its calibrated run, its paper day, its real trades) and what became of it, each trade on each
// track, where the day's result went between them (the bridge), and the day drawn with each track's trades over the
// rule's bars. GET /api/ta/rules/{id}/live-check/days/{day}, and the rule's day (GET /api/ta/rules/{id}/days/{day}/indicator).

import { useQuery } from '@tanstack/react-query'
import { Badge, ButtonLink, formatDay, formatTime, KpiStrip, PageHeader, Panel, QueryState, Skeleton } from 'ioiti-ui'
import { lazy, Suspense, useMemo } from 'react'
import { Link, useParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { useSession } from '@/features/auth/auth-context'
import { positionLink } from '@/features/positions/orderLife'
import { api, isNotFound } from '@/lib/api'
import type { LiveCheckDay, LiveCheckTrack } from '@/lib/types'

import { BridgeSegment } from './LiveCheckPanel'
import {
  apartSaid,
  fateSaid,
  liveCheckLayer,
  liveTimesSaid,
  pointsText,
  TRACK_HINT,
  TRACK_LABEL,
  TRACKS,
  tradeSaid,
} from './liveCheckModel'

// Vela only once the day is drawn.
const RunDayChart = lazy(() => import('@/features/ta/chart/RunDayChart').then((module) => ({ default: module.RunDayChart })))

const DAY = /^\d{4}-\d{2}-\d{2}$/
const RULE_NOT_FOUND = {
  title: 'No such signal rule',
  description: 'No signal rule has this address: it may have been deleted, or the address is mistyped.',
  suggestions: [{ label: 'Signal rules', href: '/ta/signals', description: 'The rules the TA engine runs' }],
}

function time(value: string | null | undefined, timeZone: string): string {
  return value ? formatTime(value, timeZone) : '—'
}

/** Each track's runs of the day, as links: the day page of the backtest, the calibrated run and paper. */
function RunsOfTheDay({ found }: { found: LiveCheckDay }) {
  const runs = (['backtest', 'calibrated', 'paper'] as const).filter((track) => found.runs[track])
  if (runs.length === 0) return null
  return (
    <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {runs.map((track) => (
        <Link key={track} className="underline-offset-2 hover:underline" to={`/backtests/${found.runs[track]}/days/${found.day}`}>
          {TRACK_LABEL[track]}: its run’s day
        </Link>
      ))}
    </p>
  )
}

function Signals({ found, timeZone }: { found: LiveCheckDay; timeZone: string }) {
  const tracks = TRACKS.filter((track) => found.present.includes(track))
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[44rem] text-left text-xs">
        <caption className="sr-only">Each signal of the day on each track</caption>
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">Bar</th>
            <th className="py-2 pr-4 font-medium">Alert</th>
            {tracks.map((track) => (
              <th key={track} className="py-2 pr-4 font-medium">{TRACK_LABEL[track]}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {found.signals.map((signal) => (
            <tr key={signal.key} className="align-top">
              <td className="py-2 pr-4 tabular-nums">
                {time(signal.at, timeZone)}
                {signal.near && <div className="text-muted-foreground">the engine’s bar {signal.near.seconds > 0 ? '+' : ''}{signal.near.seconds} s</div>}
              </td>
              <td className="py-2 pr-4">
                {signal.title}
                <div className="text-muted-foreground">{signal.action}</div>
              </td>
              {tracks.map((track) => {
                const side = signal[track]
                const times = track === 'live' ? liveTimesSaid(side) : null
                return (
                  <td key={track} className="py-2 pr-4">
                    {fateSaid(side)}
                    {times && <div className="text-muted-foreground">{times}</div>}
                    {!side && signal.why_not_raised && (track === 'paper' || track === 'live') && (
                      <div className="text-muted-foreground">{signal.why_not_raised}</div>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Trades({ found, timeZone }: { found: LiveCheckDay; timeZone: string }) {
  const tracks = TRACKS.filter((track) => found.present.includes(track))
  if (found.trades.length === 0) return <p className="text-sm text-muted-foreground">No trade on any track that day.</p>
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[44rem] text-left text-xs">
        <caption className="sr-only">Each trade of the day on each track</caption>
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">Signal</th>
            {tracks.map((track) => (
              <th key={track} className="py-2 pr-4 font-medium">{TRACK_LABEL[track]}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {found.trades.map((row) => (
            <tr key={row.key} className="align-top">
              <td className="py-2 pr-4">
                <span className="tabular-nums">{time(row.at, timeZone)}</span> {row.title}
                {row.same_turbo === false && <div><Badge tone="warning">Another turbo live</Badge></div>}
              </td>
              {tracks.map((track) => {
                const trade = row[track]
                const apart = track !== 'paper' ? apartSaid(trade, row.paper) : null
                return (
                  <td key={track} className="py-2 pr-4">
                    {trade ? (
                      <>
                        <div className="tabular-nums">{tradeSaid(trade)}</div>
                        <div className="text-muted-foreground">
                          {time(trade.filled_at, timeZone)} to {time(trade.exit_at, timeZone)}, {trade.exit_group}
                        </div>
                        {track === 'live' && trade.decided_price != null && (
                          <div className="text-muted-foreground">its sale decided at {trade.decided_price}</div>
                        )}
                        {apart && <div className="text-muted-foreground">{apart}</div>}
                        {track === 'live' && trade.id != null && (
                          <Link className="underline-offset-2 hover:underline" to={positionLink(String(trade.id))}>
                            The position
                          </Link>
                        )}
                      </>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function LiveCheckDayPage() {
  const { id = '', day = '' } = useParams()
  const { timezone, backtests_enabled: enabled } = useSession()
  const valid = DAY.test(day)
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, refetchOnWindowFocus: false })
  const rule = rulesQuery.data?.rules.find((entry) => entry.id === id)
  const query = useQuery({
    queryKey: ['live-check-day', id, day],
    queryFn: () => api.liveCheckDay(id, day),
    enabled: enabled && valid && Boolean(rule),
    retry: false,
  })
  const chartQuery = useQuery({
    queryKey: ['ta-rule-day', id, day],
    queryFn: () => api.taRuleDay(id, day),
    enabled: valid && Boolean(rule),
    retry: false,
    staleTime: 60_000,
  })
  const found = query.data
  const layer = useMemo(() => liveCheckLayer(found?.trades ?? [], found?.present ?? []), [found])

  if ((rulesQuery.data && !rule) || !valid || isNotFound(query.error)) return <NotFoundState {...RULE_NOT_FOUND} />

  const tracks: LiveCheckTrack[] = found ? TRACKS.filter((track) => found.present.includes(track)) : []
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={`${formatDay(day)}: live, paper and backtest`}
        description="Each signal and each trade of the day on each track, and where the day’s result went between them."
        parents={rule ? [{ label: rule.name, href: `/ta/signals/${id}` }] : []}
        actions={
          <ButtonLink to={`/ta/signals/${id}#live-check`} variant="outline" size="sm">
            All its days
          </ButtonLink>
        }
      />
      {!enabled ? (
        <p className="text-sm text-muted-foreground">
          The backtests are switched off (<code>admin.backtests_enabled</code>): nothing is set beside its live days.
        </p>
      ) : (
        <QueryState isPending={query.isPending} error={query.error} hasData={Boolean(found)}>
          {found && (
            <div className="flex flex-col gap-6">
              <KpiStrip
                items={tracks.map((track) => {
                  const figures = found.figures[track]
                  return {
                    label: TRACK_LABEL[track],
                    value: figures ? pointsText(figures.sum) : '—',
                    hint: figures ? `${figures.trades} trade${figures.trades === 1 ? '' : 's'} of ${figures.entry_signals} entry signal${figures.entry_signals === 1 ? '' : 's'}` : TRACK_HINT[track],
                  }
                })}
              />
              <RunsOfTheDay found={found} />
              {(found.bridge.to_paper || found.bridge.to_live) && (
                <Panel title="Where the day’s result went" description="Each trade in one place: the parts add up from one track to the next.">
                  <div className="grid gap-5 lg:grid-cols-2">
                    {found.bridge.to_paper && <BridgeSegment segment={found.bridge.to_paper} />}
                    {found.bridge.to_live && <BridgeSegment segment={found.bridge.to_live} />}
                  </div>
                </Panel>
              )}
              <Panel title="The day drawn" description="The rule’s script on its bars that day, each track’s purchases (arrows) and sales (dots): B backtest, C calibrated, P paper, L live.">
                <QueryState isPending={chartQuery.isPending} error={chartQuery.error} hasData={Boolean(chartQuery.data)}>
                  {chartQuery.data && (
                    <Suspense fallback={<Skeleton className="h-[28rem] w-full" />}>
                      <RunDayChart
                        answer={chartQuery.data}
                        day={day}
                        signals={[]}
                        trades={[]}
                        selected={null}
                        timeZone={timezone}
                        label={`${rule?.name ?? 'The rule'} on ${formatDay(day)}`}
                        layer={layer}
                      />
                    </Suspense>
                  )}
                </QueryState>
              </Panel>
              <Panel title="Its trades" description="Each track’s trade of each entry signal: its prices, when it was held, how it ended, and how far from paper.">
                <Trades found={found} timeZone={timezone} />
              </Panel>
              <Panel title="Its signals" description="What became of each signal on each track; why the engine raised nothing at a bar the backtest alerted on.">
                <Signals found={found} timeZone={timezone} />
              </Panel>
            </div>
          )}
        </QueryState>
      )}
    </div>
  )
}
