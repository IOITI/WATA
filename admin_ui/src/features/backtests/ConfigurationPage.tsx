// A configuration's own page (/backtests/configurations/<id>): the runs whose result is decided by the same things but
// their days, its latest result at the top, its nightly re-run, what each evening's run found, what it runs, every run
// of it, and the configurations of its strategy with other settings. Its address outlives its nightly runs, which the
// next one replaces.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  Button,
  ButtonLink,
  formatDateTime,
  formatDay,
  formatPercent,
  formatSpan,
  Hint,
  InlineEdit,
  PageHeader,
  Panel,
  QueryState,
  SignedValue,
  Sparkline,
} from 'ioiti-ui'
import { CalendarClock, Moon, RotateCcw, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { useSession } from '@/features/auth/auth-context'
import { api, isNotFound } from '@/lib/api'
import type { BacktestConfiguration, BacktestConfigurationPage as PageAnswer } from '@/lib/types'

import { differencesSaid } from './applyToLive'
import {
  bringUpToDate,
  CONFIGURATION_NOT_FOUND,
  configurationHref,
  configurationKey,
  daysBetween,
  distinctSaid,
  earlierStress,
  historyTotals,
  lastSentence,
  nextSentence,
  nightlySentence,
} from './configurations'
import { ConfigurationBadges, ConfigurationRunLine } from './ConfigurationsTable'
import { isActive, showValue, sourceLabel } from './model'
import { NightlyDialog } from './NightlyDialog'
import { BacktestsDisabled } from './parts'
import { useRemoveRuns } from './pending-removals-context'
import { RunFacts } from './RunFacts'
import { CurvesPanel, RunKpis } from './runPanels'
import { VerdictPanel } from './VerdictPanel'

/** Its nightly re-run: whether it is on, its last run and its next, and the switch. */
function NightlyPanel({ item, onSwitch }: { item: BacktestConfiguration; onSwitch: () => void }) {
  const nightly = item.nightly
  return (
    <Panel
      title="Each evening"
      action={
        item.why_no_nightly ? null : (
          <Button size="sm" variant="outline" leftIcon={<Moon className="size-3.5" />} onClick={onSwitch}>
            {nightly?.on ? 'Change' : 'Switch on'}
          </Button>
        )
      }
    >
      {item.why_no_nightly ? (
        <p className="text-sm text-muted-foreground">{item.why_no_nightly}.</p>
      ) : (
        <div className="flex flex-col gap-1.5 text-sm">
          <p>{nightlySentence(nightly)}</p>
          {nightly?.on && <p className="text-text-secondary">{nextSentence(nightly)}</p>}
          {nightly?.last && <p className="text-text-secondary">{lastSentence(nightly)}</p>}
          {nightly?.on && nightly.telegram && <p className="text-xs text-muted-foreground">Its result is said on Telegram once each run ends.</p>}
        </div>
      )}
    </Panel>
  )
}

/** What each evening's run found, the newest first: the run itself is replaced, its figures stay. */
function HistoryPanel({ page, timezone }: { page: PageAnswer; timezone: string }) {
  const totals = historyTotals(page.history)
  if (page.history.length === 0) return null
  return (
    <Panel
      title="What each evening found"
      description="The result of each nightly run as it was, its days growing one trading day at a time."
      action={
        totals.length > 1 ? (
          <Sparkline values={totals} label="Its total, evening after evening" format={(value) => formatPercent(value, true)} />
        ) : null
      }
    >
      <div className="relative overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-1 pr-4 font-medium">Made</th>
              <th className="py-1 pr-4 font-medium">Days</th>
              <th className="py-1 pr-4 text-right font-medium">Trades</th>
              <th className="py-1 pr-4 text-right font-medium">All days</th>
              <th className="py-1 pr-4 text-right font-medium">Worst drawdown</th>
              <th className="py-1 text-right font-medium">Beats random</th>
            </tr>
          </thead>
          <tbody>
            {page.history.map((line) => (
              <tr key={line.run} className="border-t border-border">
                <td className="py-1 pr-4 whitespace-nowrap">{formatDateTime(line.made_at, timezone, false)}</td>
                <td className="py-1 pr-4 whitespace-nowrap">
                  {formatDay(line.from, false)} to {formatDay(line.to, false)}
                  {line.days !== null && <span className="text-muted-foreground"> · {line.days} traded</span>}
                </td>
                <td className="py-1 pr-4 text-right">{line.trades ?? '-'}</td>
                <td className="py-1 pr-4 text-right">
                  <SignedValue value={line.total_percent} kind="percent" />
                </td>
                <td className="py-1 pr-4 text-right">
                  <SignedValue value={line.worst_drawdown} kind="percent" />
                </td>
                <td className="py-1 text-right">{formatPercent(line.beaten, false, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

export function ConfigurationPage() {
  const { configId = '' } = useParams()
  const { timezone, backtests_enabled: enabled } = useSession()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const removeRuns = useRemoveRuns()
  // The nightly dialog opened from a run's page (?nightly=open).
  const [switching, setSwitching] = useState(params.get('nightly') === 'open')
  const closeSwitch = () => {
    setSwitching(false)
    if (params.has('nightly')) setParams({}, { replace: true })
  }

  const pageQuery = useQuery({
    queryKey: configurationKey(configId),
    queryFn: () => api.backtestConfiguration(configId),
    enabled,
    refetchInterval: (query) => (query.state.data?.configuration.runs.some((run) => isActive(run.state)) ? 5_000 : 30_000),
  })
  const page = pageQuery.data
  const item = page?.configuration
  const latest = item?.latest ?? null
  const latestRunQuery = useQuery({
    queryKey: ['backtest', latest?.id],
    queryFn: () => api.backtest(latest?.id ?? ''),
    enabled: !!latest,
    staleTime: 60_000,
  })
  const latestRun = latestRunQuery.data
  const single = latestRun?.kind === 'run' && latestRun.status.state === 'done' && !latestRun.paper
  const toLiveQuery = useQuery({
    queryKey: ['backtest-to-live', latest?.id],
    queryFn: () => api.backtestToLive(latest?.id ?? ''),
    enabled: !!single,
    staleTime: 60_000,
  })
  const optionsQuery = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, enabled, staleTime: 60_000 })
  const fields = useMemo(() => optionsQuery.data?.fields ?? [], [optionsQuery.data])
  const fieldOf = useMemo(() => new Map(fields.map((field) => [field.name, field])), [fields])

  const shown = (answer: PageAnswer) => {
    queryClient.setQueryData(configurationKey(configId), answer)
    void queryClient.invalidateQueries({ queryKey: ['backtests'], exact: true })
    void queryClient.invalidateQueries({ queryKey: ['backtests', 'configurations'], exact: true })
  }
  const rename = useMutation({ mutationFn: (name: string | null) => api.changeBacktestConfiguration(configId, { name }), onSuccess: shown })
  const extend = useMutation({ mutationFn: () => api.extendBacktestConfiguration(configId), onSuccess: shown })

  if (isNotFound(pageQuery.error)) return <NotFoundState {...CONFIGURATION_NOT_FOUND} />

  const bring = item && page ? bringUpToDate(item, page.day) : null
  // The runs it may let go of by hand: those before its latest result, made by hand, nothing resting on them.
  const older = item ? item.runs.filter((run) => run.id !== latest?.id && !run.nightly && run.kept.length === 0 && !isActive(run.state)) : []
  const stress = item ? earlierStress(item) : null

  return (
    <>
      <PageHeader
        title={item?.name ?? 'Configuration'}
        description={item ? `${sourceLabel(item)}${item.turbos === 'modelled' ? ' · made-up turbos' : ''} · ${item.count} run${item.count === 1 ? '' : 's'}` : undefined}
        actions={
          item &&
          bring && (
            <>
              <Hint content={bring.why} wrap={!bring.can}>
                <Button
                  size="sm"
                  variant="outline"
                  leftIcon={<CalendarClock className="size-3.5" />}
                  disabled={!bring.can}
                  isLoading={extend.isPending}
                  onClick={() => extend.mutate()}
                >
                  Bring it up to date
                </Button>
              </Hint>
              {latest && (
                <ButtonLink to={`/backtests/new?again=${latest.id}`} size="sm" leftIcon={<RotateCcw className="size-3.5" />}>
                  Run again with changes
                </ButtonLink>
              )}
            </>
          )
        }
      />
      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState isPending={pageQuery.isPending} error={pageQuery.error} hasData={!!page}>
          {page && item && (
            <div className="flex flex-col gap-6">
              <div className="flex flex-col gap-2">
                <InlineEdit
                  label="Name of the configuration"
                  value={item.name}
                  onSave={(name) => rename.mutateAsync(name.trim() || null).then(() => undefined)}
                />
                <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
                  <ConfigurationBadges item={item} />
                  {item.distinct.length > 0 && <span>Set apart from the others of its strategy by {distinctSaid(item.distinct)}.</span>}
                  {item.named && (
                    <Button variant="ghost" size="sm" onClick={() => rename.mutate(null)}>
                      Name it after its runs
                    </Button>
                  )}
                </p>
                {(rename.error || extend.error) && (
                  <Alert tone="danger" role="alert">
                    <AlertDescription>{(rename.error ?? extend.error)?.message}</AlertDescription>
                  </Alert>
                )}
              </div>

              <NightlyPanel item={item} onSwitch={() => setSwitching(true)} />

              {latest ? (
                <Panel
                  title={`Its latest result: ${formatDay(latest.from)} to ${formatDay(latest.to)}`}
                  description={`${daysBetween(latest.from, latest.to)} days asked${latest.nightly ? (item.nightly?.on ? ', made again each evening' : ', its last nightly run') : ''}.`}
                  action={
                    <ButtonLink to={`/backtests/${latest.id}`} size="sm" variant="outline">
                      Open the run
                    </ButtonLink>
                  }
                >
                  {latestRun?.summary ? <RunKpis summary={latestRun.summary} /> : <p className="text-sm text-muted-foreground">Reading it…</p>}
                </Panel>
              ) : (
                <Alert tone="neutral">
                  <AlertDescription>No run of it has finished yet.</AlertDescription>
                </Alert>
              )}

              {latestRun && latestRun.status.state === 'done' && <VerdictPanel run={latestRun} />}
              {stress && (
                <p className="text-sm text-muted-foreground">
                  Its latest run is not stressed yet; an earlier one was, to {formatDay(stress.to, false)}:{' '}
                  <Link to={`/backtests/${stress.id}`} className="font-medium text-foreground underline-offset-4 hover:underline">
                    see that stress
                  </Link>
                  .
                </p>
              )}
              {latestRun?.summary && latestRun.kind === 'run' && <CurvesPanel summary={latestRun.summary} />}

              <HistoryPanel page={page} timezone={timezone} />

              {latestRun && (
                <div className="flex flex-col gap-2">
                  <RunFacts run={latestRun} labels={fields} />
                  {toLiveQuery.data && (
                    <p className="text-sm text-muted-foreground">
                      {differencesSaid(toLiveQuery.data.differences)}{' '}
                      {toLiveQuery.data.differences.length > 0 && (
                        <Link to={`/backtests/${latestRun.id}`} className="font-medium text-foreground underline-offset-4 hover:underline">
                          Apply them from its latest run
                        </Link>
                      )}
                    </p>
                  )}
                </div>
              )}

              <Panel
                title={`Its runs (${item.count})`}
                description="The newest first; a nightly run is replaced by the next once that one is done, unless something rests on it."
                action={
                  older.length > 0 ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      leftIcon={<Trash2 className="size-3.5" />}
                      onClick={() => removeRuns(older.map((run) => ({ id: run.id, name: run.name })))}
                    >
                      Remove the {older.length} older run{older.length === 1 ? '' : 's'}
                    </Button>
                  ) : null
                }
              >
                <ul className="flex flex-col gap-1.5">
                  {item.runs.map((run) => (
                    <ConfigurationRunLine key={run.id} run={run} timezone={timezone} />
                  ))}
                </ul>
              </Panel>

              {page.related.length > 0 && (
                <Panel title="Same strategy, other settings" description="The configurations of the same script, inputs, asset and bars.">
                  <ul className="flex flex-col gap-2 text-sm">
                    {page.related.map((other) => (
                      <li key={other.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        <Link to={configurationHref(other.id)} className="font-medium underline-offset-4 hover:underline">
                          {other.name}
                        </Link>
                        <span className="text-muted-foreground">
                          {other.count} run{other.count === 1 ? '' : 's'}
                          {other.latest?.headline?.total_percent !== null && other.latest?.headline?.total_percent !== undefined && (
                            <>
                              {' '}
                              · <SignedValue value={other.latest.headline.total_percent} kind="percent" /> to {formatDay(other.latest.to, false)}
                            </>
                          )}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {other.differences
                            .slice(0, 4)
                            .map((difference) => {
                              const field = fieldOf.get(difference.name)
                              return `${field?.label ?? difference.name}: ${showValue(difference.there, field)} there, ${showValue(difference.here, field)} here`
                            })
                            .join(' · ')}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Panel>
              )}

              {item.nightly?.last?.seconds !== null && item.nightly?.last?.seconds !== undefined && (
                <p className="text-xs text-muted-foreground">Its last nightly run took {formatSpan(item.nightly.last.seconds)}.</p>
              )}
            </div>
          )}
        </QueryState>
      )}
      {item && <NightlyDialog key={`${item.id}-${switching}`} item={item} open={switching} telegram={page?.telegram ?? null} onClose={closeSwitch} />}
    </>
  )
}
