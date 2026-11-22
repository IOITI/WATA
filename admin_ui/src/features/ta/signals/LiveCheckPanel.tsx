// A signal rule's real trades beside its paper days, the backtest configuration linked to it and that configuration's
// calibrated run (src/admin/live_check.py): day by day, where the result went between them (the bridge, in points of the
// turbo, which adds up), and what the live trades measure for the next backtest. GET /api/ta/rules/{id}/live-check.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  ButtonLink,
  cn,
  formatDateTime,
  formatDay,
  formatPercent,
  KpiStrip,
  Panel,
  QueryState,
  Select,
} from 'ioiti-ui'
import { useState } from 'react'
import { Link } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { configurationHref } from '@/features/backtests/configurations'
import { RunPeekLink } from '@/features/backtests/RunPeek'
import { api } from '@/lib/api'
import type { LiveCheck, LiveCheckSegment, TaRule } from '@/lib/types'

import {
  agreementSaid,
  bridgeBars,
  calibratedRunSaid,
  calibratedSaid,
  calibrationRows,
  dayRow,
  liveCheckDayHref,
  pointsText,
  segmentSaid,
  shownTracks,
  slippageSaid,
  strategySaid,
  TRACK_HINT,
  TRACK_LABEL,
} from './liveCheckModel'

const STRATEGY_TONE = { success: 'success', danger: 'danger', neutral: 'neutral' } as const
// The days the table lists, newest first: the rest stays on the page of each.
const SHOWN_DAYS = 30

/** A segment of the bridge: where the result went from one track to the next, bucket by bucket, as signed bars. */
export function BridgeSegment({ segment }: { segment: LiveCheckSegment }) {
  const bars = bridgeBars(segment)
  return (
    <section aria-label={`From ${TRACK_LABEL[segment.from].toLowerCase()} to ${TRACK_LABEL[segment.to].toLowerCase()}`} className="space-y-2">
      <h4 className="text-sm font-medium">
        From {TRACK_LABEL[segment.from].toLowerCase()} to {TRACK_LABEL[segment.to].toLowerCase()}
      </h4>
      <p className="text-xs text-muted-foreground">{segmentSaid(segment)}</p>
      {bars.length > 0 && (
        <ul className="space-y-2">
          {bars.map((bar) => (
            <li key={bar.id} className="space-y-1 text-xs">
              <div className="flex items-baseline justify-between gap-3">
                <span>{bar.label}</span>
                <span className={cn('whitespace-nowrap tabular-nums', bar.points >= 0 ? 'text-positive' : 'text-negative')}>
                  {pointsText(bar.points)} · {bar.trades} trade{bar.trades === 1 ? '' : 's'}
                </span>
              </div>
              <div className="h-1.5 rounded-sm bg-muted" aria-hidden>
                <div
                  className={cn('h-full rounded-sm', bar.points >= 0 ? 'bg-positive/70' : 'bg-negative/70')}
                  style={{ width: `${Math.max(2, Math.round(bar.share * 100))}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** The configuration linked as the rule's backtest, whether it plays its strategy, and its nightly; or a way to link one. */
function LinkedBacktest({ rule, check }: { rule: TaRule; check: LiveCheck }) {
  const queryClient = useQueryClient()
  const [choosing, setChoosing] = useState(false)
  const [chosen, setChosen] = useState('')
  const candidates = useQuery({
    queryKey: ['live-check-candidates', rule.id],
    queryFn: () => api.liveCheckCandidates(rule.id),
    enabled: choosing,
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['live-check', rule.id] })
  const link = useMutation({
    mutationFn: (configuration: string) => api.linkLiveCheck(rule.id, configuration),
    onSuccess: async () => {
      setChoosing(false)
      await refresh()
    },
  })
  const unlink = useMutation({ mutationFn: () => api.unlinkLiveCheck(rule.id), onSuccess: refresh })
  const nightly = useMutation({
    mutationFn: (configuration: string) => api.changeBacktestConfiguration(configuration, { nightly: { on: true } }),
    onSuccess: refresh,
  })

  if (!check.link || choosing) {
    const items = candidates.data?.items ?? []
    return (
      <div className="space-y-2 text-sm">
        <p className="text-muted-foreground">
          {check.link ? 'Another backtest for it:' : 'No backtest is linked to it yet.'} A configuration whose runs play its
          strategy (the same script source, chart, alerts, history and inputs) is set beside its paper and live days, and made
          again each evening with what the live trades measured.
        </p>
        {!choosing ? (
          <Button size="sm" variant="outline" onClick={() => setChoosing(true)}>
            Link a backtest
          </Button>
        ) : (
          <QueryState isPending={candidates.isPending} error={candidates.error} hasData={Boolean(candidates.data)}>
            {items.length === 0 ? (
              <p className="text-muted-foreground">No configuration plays its strategy: make a backtest from the rule first.</p>
            ) : (
              <div className="flex flex-wrap items-end gap-2">
                <div className="w-full max-w-md">
                  <Select
                    aria-label="The configuration to link"
                    value={chosen || items[0].configuration}
                    onChange={(event) => setChosen(event.target.value)}
                    options={items.map((item) => ({
                      value: item.configuration,
                      label: `${item.name ?? item.configuration} (${item.from ?? '?'} to ${item.to ?? '?'})`,
                    }))}
                  />
                </div>
                <Button size="sm" isLoading={link.isPending} onClick={() => link.mutate(chosen || items[0].configuration)}>
                  Link it
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setChoosing(false)}>
                  Cancel
                </Button>
              </div>
            )}
          </QueryState>
        )}
        {link.error && (
          <Alert tone="danger" role="alert">
            <AlertDescription>{link.error.message}</AlertDescription>
          </Alert>
        )}
      </div>
    )
  }

  const strategy = strategySaid(check.strategy)
  return (
    <div className="space-y-2 text-sm">
      <p>
        Its backtest:{' '}
        <Link className="font-medium underline-offset-2 hover:underline" to={configurationHref(check.link.configuration)}>
          {check.template?.name ?? check.link.configuration}
        </Link>
        {check.template && (
          <>
            {', its newest run '}
            <RunPeekLink id={check.template.id} className="underline-offset-2 hover:underline">
              {check.template.from} to {check.template.to}
            </RunPeekLink>
          </>
        )}
        .
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={STRATEGY_TONE[strategy.tone]}>{check.strategy?.same ? 'Same strategy' : check.strategy ? 'Another strategy' : 'Not checked'}</Badge>
        <span className="text-xs text-muted-foreground">{strategy.text}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {check.link.nightly.on ? (
          <span>Made again after each trading day (its nightly is on), so each live day has its backtest.</span>
        ) : (
          <>
            <span>Its nightly is off: the days after its newest run have no backtest.</span>
            <Button size="sm" variant="outline" isLoading={nightly.isPending} onClick={() => nightly.mutate(check.link!.configuration)}>
              Switch its nightly on
            </Button>
          </>
        )}
        <Button size="sm" variant="ghost" onClick={() => setChoosing(true)}>
          Another
        </Button>
        <Button size="sm" variant="ghost" isLoading={unlink.isPending} onClick={() => unlink.mutate()}>
          Unlink
        </Button>
      </div>
      {(nightly.error || unlink.error) && (
        <Alert tone="danger" role="alert">
          <AlertDescription>{(nightly.error ?? unlink.error)?.message}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}

/** What the live trades measure for the next backtest, and the calibrated run made with it. */
function Calibration({ check, timeZone }: { check: LiveCheck; timeZone: string }) {
  const now = check.calibration.now
  const run = check.calibrated?.run
  return (
    <section aria-label="What the live trades measure" className="space-y-2">
      <h3 className="text-sm font-medium">What the live trades measure</h3>
      {now ? (
        <>
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-2 pr-4 font-medium">Setting</th>
                  <th className="py-2 pr-4 font-medium">The backtest’s</th>
                  <th className="py-2 pr-4 font-medium">Measured live</th>
                  <th className="py-2 font-medium">In the calibrated run</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {calibrationRows(now).map((row) => (
                  <tr key={row.name}>
                    <td className="py-2 pr-4 font-medium">{row.label}</td>
                    <td className="py-2 pr-4 tabular-nums">{row.backtest}</td>
                    <td className="py-2 pr-4 tabular-nums">{row.measured}</td>
                    <td className="py-2">{row.applied ? <Badge tone="success">Taken</Badge> : <span className="text-muted-foreground">Kept</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {[...now.said, slippageSaid(now)].filter(Boolean).map((said) => (
            <p key={said} className="text-xs text-muted-foreground">{said}</p>
          ))}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">Nothing measured: no backtest is linked, or the database cannot be read.</p>
      )}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="text-muted-foreground">The calibrated run: {calibratedRunSaid(check)}</span>
        {run && (
          <RunPeekLink id={run} className="underline-offset-2 hover:underline">
            Its run
          </RunPeekLink>
        )}
        {run && check.calibrated?.state === 'done' && (
          <ButtonLink to={`/backtests/new?again=${run}`} variant="outline" size="sm">
            Make a run with them
          </ButtonLink>
        )}
      </div>
      {check.calibration.history.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">What the evenings measured ({check.calibration.history.length})</summary>
          <ul className="mt-2 space-y-1">
            {check.calibration.history.slice(0, 10).map((line) => (
              <li key={line.at}>
                {formatDateTime(line.at, timeZone)}: {Object.entries(line.values).map(([name, value]) => `${name} ${value}`).join(', ') || 'nothing'}
                {line.measured.signal_delay_seconds.count > 0 && `, from ${line.measured.signal_delay_seconds.count} live entries`}
                {line.error ? ` (${line.error})` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

export function LiveCheckPanel({ rule, enabled, className }: { rule: TaRule; enabled: boolean; className?: string }) {
  const { timezone } = useSession()
  const query = useQuery({ queryKey: ['live-check', rule.id], queryFn: () => api.liveCheck(rule.id), enabled, staleTime: 60_000 })
  const check = query.data

  return (
    <Panel
      id="live-check"
      className={className}
      title="Live, paper and backtest"
      description="Its real trades beside its paper days and its backtest, day by day; where the result went between them, in points of the turbo (each trade in one place, so it adds up); and the backtest made again with what live measured."
    >
      {!enabled ? (
        <p className="text-sm text-muted-foreground">
          The backtests are switched off (<code>admin.backtests_enabled</code>): nothing is set beside its live days.
        </p>
      ) : (
        <QueryState isPending={query.isPending} error={query.error} hasData={Boolean(check)}>
          {check && (
            <div className="flex flex-col gap-5">
              <LinkedBacktest rule={rule} check={check} />
              {!check.database && (
                <Alert tone="warning">
                  <AlertDescription>The database cannot be read: its live trades are not known now.</AlertDescription>
                </Alert>
              )}
              <Summary check={check} />
              <Days check={check} ruleId={rule.id} />
              <Calibration check={check} timeZone={timezone} />
            </div>
          )}
        </QueryState>
      )}
    </Panel>
  )
}

/** The days together: each track's result, how far the backtest raised the engine's signals, and the bridge. */
function Summary({ check }: { check: LiveCheck }) {
  const totals = check.summary
  const tracks = shownTracks(check.days)
  if (check.days.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No day of it yet between {formatDay(check.period.from)} and {formatDay(check.period.to)}: its days come once traded on paper,
        backtested or live.
      </p>
    )
  }
  const agreement = agreementSaid(totals.agreement)
  const calibrated = calibratedSaid(totals.calibrated)
  return (
    <div className="space-y-4">
      <KpiStrip
        items={tracks.map((track) => {
          const found = totals.tracks[track]
          return {
            label: TRACK_LABEL[track],
            value: found ? pointsText(found.sum) : '—',
            hint: found
              ? `${found.trades} trade${found.trades === 1 ? '' : 's'} over ${found.days} day${found.days === 1 ? '' : 's'}, compounded ${formatPercent(found.compounded, true)}`
              : TRACK_HINT[track],
          }
        })}
      />
      {agreement && <p className="text-xs text-muted-foreground">{agreement}</p>}
      {calibrated && <p className="text-xs text-muted-foreground">{calibrated}</p>}
      {(totals.to_paper || totals.to_live) && (
        <div className="grid gap-5 lg:grid-cols-2">
          {totals.to_paper && <BridgeSegment segment={totals.to_paper} />}
          {totals.to_live && <BridgeSegment segment={totals.to_live} />}
        </div>
      )}
    </div>
  )
}

/** Each day: each track's trades and summed result, and the gap to the backtest; a day opens on its own page. */
function Days({ check, ruleId }: { check: LiveCheck; ruleId: string }) {
  const tracks = shownTracks(check.days)
  if (check.days.length === 0) return null
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[34rem] text-left text-xs">
        <caption className="sr-only">Each day of the rule, track by track</caption>
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">Day</th>
            {tracks.map((track) => (
              <th key={track} className="py-2 pr-4 text-right font-medium">{TRACK_LABEL[track]}</th>
            ))}
            <th className="py-2 text-right font-medium">Gap to the backtest</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {check.days.slice(0, SHOWN_DAYS).map((line) => {
            const row = dayRow(line)
            return (
              <tr key={row.day}>
                <td className="whitespace-nowrap py-2 pr-4 font-medium">
                  <Link className="underline-offset-2 hover:underline" to={liveCheckDayHref(ruleId, row.day)}>
                    {formatDay(row.day)}
                  </Link>
                  {row.live && <Badge tone="success" className="ml-2">Live</Badge>}
                </td>
                {tracks.map((track) => {
                  const cell = row.cells[track]
                  return (
                    <td key={track} className="whitespace-nowrap py-2 pr-4 text-right tabular-nums">
                      {cell ? `${pointsText(cell.sum)} · ${cell.trades}` : <span className="text-muted-foreground">—</span>}
                    </td>
                  )
                })}
                <td className="whitespace-nowrap py-2 text-right tabular-nums">
                  {row.gap ? (
                    <span className={row.gap.points >= 0 ? 'text-positive' : 'text-negative'}>
                      {pointsText(row.gap.points)} <span className="text-muted-foreground">({TRACK_LABEL[row.gap.against].toLowerCase()})</span>
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {check.days.length > SHOWN_DAYS && (
        <p className="mt-2 text-xs text-muted-foreground">The newest {SHOWN_DAYS} of {check.days.length} days.</p>
      )}
    </div>
  )
}
