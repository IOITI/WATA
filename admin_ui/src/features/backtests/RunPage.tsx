import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Flag, Pencil, Radar, RotateCcw, Square, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router'

import { ButtonLink } from '@/components/ButtonLink'
import { KpiTile } from '@/components/KpiTile'
import { useMainScroll } from '@/components/main-scroll-context'
import { NotFoundState } from '@/components/NotFoundState'
import { PageHeader, Panel } from '@/components/Panel'
import { RouterLink } from '@/components/RouterLink'
import { PnlValue, QueryState } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AnimatedTabs } from '@/components/ui/animated-tabs'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/form'
import { ScrollProgressNav } from '@/components/ui/scroll-progress-nav'
import { SettleModal } from '@/components/ui/settle-modal'
import { useSession } from '@/features/auth/auth-context'
import { barsLabel } from '@/features/ta/signals/ruleModel'
import { api, isNotFound } from '@/lib/api'
import { formatDateTime, formatDay, formatMoney, formatNumber, formatPercent } from '@/lib/format'
import type { BacktestRun } from '@/lib/types'

import {
  assetLabel,
  daysSentence,
  isActive,
  progressSentence,
  RUN_NOT_FOUND,
  settingChanges,
  sourceLabel,
} from './model'
import { EdgePanel } from './EdgePanel'
import { BacktestsDisabled, ChangeList, Measure, StateBadge } from './parts'
import { ReconciliationPanel } from './ReconciliationPanel'
import { BreakdownsPanel, CurvesPanel, TrustPanel, WhyPanel } from './runPanels'
import { DaysTable, SignalsTable, TradesTable } from './runTables'
import { ModelCheckPanel } from './ModelCheckPanel'
import { choiceTrust } from './robustness'
import { StressPanel } from './StressPanel'
import { ChoicePanel, SweepPanel } from './SweepPanel'
import { underlyingLabel } from './turboModel'
import { VerdictPanel } from './VerdictPanel'
import type { PageSection } from './VerdictPanel'

/** Where a part of the page starts when the nav leads to it: below the nav, which stays at the top. */
const ANCHORED = 'scroll-mt-14'

/** The parts of a done run's page, in their order: what the index lists, each an anchor. */
function runSections(run: BacktestRun): PageSection[] {
  const summary = run.summary
  if (run.status.state !== 'done' || !summary) return []
  const judged = !run.stress_of && !run.model_check_of
  if (run.kind === 'sweep') {
    const choosing = !run.stress_of && (choiceTrust(summary).length > 0 || summary.walk_forward !== undefined)
    return [
      ...(judged ? [{ id: 'verdict', label: 'Verdict' }] : []),
      { id: 'sweep', label: 'Combinations' },
      ...(choosing ? [{ id: 'choice', label: 'Choosing' }] : []),
      { id: 'setup', label: 'What it ran' },
    ]
  }
  if (!summary.figures) return []
  return [
    ...(judged ? [{ id: 'verdict', label: 'Verdict' }] : []),
    { id: 'trust', label: 'Trust' },
    ...(run.request.source.kind === 'journal' ? [{ id: 'reconciliation', label: 'Real trades' }] : []),
    { id: 'edge', label: 'Edge' },
    { id: 'stress', label: 'Stress' },
    ...(hasModelCheck(run) ? [{ id: 'model-check', label: 'Made-up turbos' }] : []),
    { id: 'curves', label: 'Day after day' },
    { id: 'why', label: 'Why' },
    { id: 'breakdowns', label: 'Where it comes from' },
    { id: 'tables', label: 'Trades, signals, days' },
    { id: 'setup', label: 'What it ran' },
  ]
}

/** As ModelCheckPanel shows itself: on a model check, or on a run of the recorded turbos. */
function hasModelCheck(run: BacktestRun): boolean {
  return Boolean(run.model_check_of) || (run.turbos ?? 'recorded') === 'recorded'
}

function Progress({ run }: { run: BacktestRun }) {
  const progress = run.progress
  const share = progress && progress.total > 0 ? Math.min(1, progress.done / progress.total) : 0
  return (
    <Panel title={run.status.state === 'queued' ? 'Waiting its turn' : 'Running'}>
      <div className="flex flex-col gap-3 text-sm">
        <p>
          {run.status.state === 'queued'
            ? `One backtest runs at a time: this one is number ${run.position ?? '?'} in line.`
            : progressSentence(progress)}
        </p>
        {run.status.state === 'running' && (
          <div className="h-1.5 w-full max-w-md rounded-full bg-foreground/10" role="progressbar" aria-valuenow={Math.round(share * 100)} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-1.5 rounded-full bg-foreground/60 transition-all" style={{ width: `${Math.max(share * 100, 3)}%` }} />
          </div>
        )}
        {run.lines.length > 0 && (
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs text-muted-foreground">{run.lines.join('\n')}</pre>
        )}
      </div>
    </Panel>
  )
}

/** What a run was asked: its signals, its asset, its days, and what it changed of WATA's settings. */
function RunFacts({ run, labels }: { run: BacktestRun; labels: Parameters<typeof settingChanges>[2] }) {
  const request = run.request
  const source = request.source
  const engine = run.summary?.made_with.engine
  const turbos = run.summary?.made_with.turbos
  const modelled = turbos && turbos.kind === 'modelled' ? turbos : null
  const execution = run.summary?.made_with.execution
  const drawn = execution && execution.kind === 'measured' ? execution : null
  return (
    <Panel title="What it ran">
      <div className="grid gap-8 lg:grid-cols-2">
        <dl className="flex flex-col gap-1.5">
          <Measure label="Signals">
            {source.kind === 'pine' ? (
              <>
                <Link to={`/ta/indicators/${source.rule.script.id}`} className="underline-offset-4 hover:underline">
                  {source.rule.name}
                </Link>{' '}
                v{source.rule.script.version}
                {source.from_rule ? ` (the rule ${source.from_rule.name})` : ''}
              </>
            ) : (
              `The ${source.signals} signal${source.signals === 1 ? '' : 's'} WATA received`
            )}
          </Measure>
          <Measure label="Asset">
            {assetLabel(request.chart.asset)} · {barsLabel(request.chart.bars)} · {request.chart.side}
          </Measure>
          <Measure label="Turbos of">{request.indice}</Measure>
          {modelled && (
            <Measure label="Model">
              <span className="text-xs text-warning">
                Turbos made up from {underlyingLabel(modelled.underlying)}, measured on {modelled.days} recorded day{modelled.days === 1 ? '' : 's'}
                {modelled.few_days ? ': few, a rough guess' : ''}. Check it against the recorded days.
              </span>
            </Measure>
          )}
          <Measure label="Period">
            {formatDay(request.from)} to {formatDay(request.to)}
          </Measure>
          {source.kind === 'pine' && source.rule.inputs.length > 0 && (
            <Measure label="Inputs">{source.rule.inputs.map((entry) => `${entry.title ?? entry.id} ${String(entry.value)}`).join(', ')}</Measure>
          )}
          {source.kind === 'pine' && (
            <Measure label="Alerts">{source.rule.alerts.map((entry) => `“${entry.title}” → ${entry.action}`).join('; ') || 'alert() calls'}</Measure>
          )}
          <Measure label="Asked">{formatDateTime(run.status.created_at, 'UTC', false)} UTC</Measure>
          {run.summary && (
            <Measure label="Made with">
              WATA {run.summary.made_with.version}
              {engine ? `, PineTS ${engine.runner.pinets} (${formatNumber(engine.milliseconds / 1000, 1)} s of script)` : ''}
              {drawn && (
                <span className="block text-xs text-muted-foreground">
                  Each order’s time and price drawn from {formatNumber(drawn.orders)} of WATA’s own, from {formatDay(drawn.from)} to{' '}
                  {formatDay(drawn.to)}
                </span>
              )}
              {run.stale && <span className="block text-xs text-warning">Other code than this admin’s: it could come out otherwise today.</span>}
            </Measure>
          )}
        </dl>
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-muted-foreground">Changed from WATA’s settings</h3>
          <ChangeList changes={settingChanges(run.settings.changed, run.settings.live_before, labels)} none="Nothing: the live settings of the day it was asked." />
        </div>
      </div>
    </Panel>
  )
}

// The tables of a run: each tab has its address (`?tab=`), and can be opened in another tab.
const TABLES = ['trades', 'signals', 'days'] as const
type Table = (typeof TABLES)[number]

function tableOf(value: string | null): Table {
  return TABLES.find((table) => table === value) ?? 'trades'
}

/** The address of a table of the run, as a tab's link: the first one is the run's own address. */
function tableHref(table: Table): string {
  return table === 'trades' ? '?' : `?tab=${table}`
}

export function RunPage() {
  const { runId = '' } = useParams()
  const { timezone, backtests_enabled: enabled } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const { hash } = useLocation()
  const tab = tableOf(params.get('tab'))
  // Going through the tables does not leave as many steps to go back through.
  const setTab = (next: string) => {
    const table = tableOf(next)
    setParams(table === 'trades' ? {} : { tab: table }, { replace: true })
  }
  const [removing, setRemoving] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState('')
  const [tags, setTags] = useState('')

  const runQuery = useQuery({
    queryKey: ['backtest', runId],
    queryFn: () => api.backtest(runId),
    enabled,
    refetchInterval: (query) => (query.state.data && isActive(query.state.data.status.state) ? 2_000 : false),
  })
  const run = runQuery.data
  const done = run?.status.state === 'done'
  const single = done && run?.kind === 'run'
  const optionsQuery = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, enabled, staleTime: 60_000 })
  const fields = optionsQuery.data?.fields ?? []
  const daysQuery = useQuery({ queryKey: ['backtest-days', runId], queryFn: () => api.backtestDays(runId), enabled: done, staleTime: Infinity })
  const tradesQuery = useQuery({ queryKey: ['backtest-trades', runId], queryFn: () => api.backtestTrades(runId), enabled: single, staleTime: Infinity })
  const signalsQuery = useQuery({ queryKey: ['backtest-signals', runId], queryFn: () => api.backtestSignals(runId), enabled: single, staleTime: Infinity })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['backtest', runId] })
  const cancel = useMutation({ mutationFn: () => api.cancelBacktest(runId), onSuccess: refresh })
  const remove = useMutation({
    mutationFn: () => api.deleteBacktest(runId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['backtests'] })
      navigate('/backtests')
    },
  })
  const reference = useMutation({
    mutationFn: () => api.setBacktestReference(runId),
    onSuccess: (paper) => queryClient.setQueryData(['backtest-paper'], paper),
  })
  const rename = useMutation({
    mutationFn: () => api.renameBacktest(runId, { name, tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean) }),
    onSuccess: () => {
      void refresh()
      setRenaming(false)
    },
  })

  const summary = run?.summary
  const figures = summary?.figures
  const money = summary?.money
  const sections = useMemo(() => (run ? runSections(run) : []), [run])
  // Numbered as the nav shows them, each a link to its place (`#edge`), which can be opened in another tab.
  const parts = useMemo(
    () => sections.map((section, index) => ({ ...section, index: String(index + 1).padStart(2, '0'), href: `#${section.id}` })),
    [sections],
  )
  const scroller = useMainScroll()

  // An address that names a part of the page (#edge) is brought to it once the run is drawn.
  const drawn = sections.length > 0
  useEffect(() => {
    if (drawn && hash) document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.()
  }, [drawn, hash])

  if (isNotFound(runQuery.error)) return <NotFoundState {...RUN_NOT_FOUND} />

  return (
    <>
      <PageHeader
        title={run?.name ?? 'Backtest'}
        description={run ? `${sourceLabel(run)} · ${formatDay(run.from)} to ${formatDay(run.to)}` : undefined}
        actions={
          run && (
            <>
              {isActive(run.status.state) ? (
                <Button variant="outline" size="sm" leftIcon={<Square className="size-3.5" />} isLoading={cancel.isPending} onClick={() => cancel.mutate()}>
                  Stop
                </Button>
              ) : (
                <Button variant="outline" size="sm" leftIcon={<Trash2 className="size-3.5" />} onClick={() => setRemoving(true)}>
                  Remove
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Pencil className="size-3.5" />}
                onClick={() => {
                  setName(run.name)
                  setTags(run.tags.join(', '))
                  setRenaming(true)
                }}
              >
                Rename
              </Button>
              {run.kind === 'run' && run.status.state === 'done' && run.source.from_rule && run.source.kind === 'pine' && (
                <Button
                  variant="outline"
                  size="sm"
                  leftIcon={<Flag className="size-3.5" />}
                  isLoading={reference.isPending}
                  disabled={reference.isSuccess}
                  title={`The run the paper days of ${run.source.from_rule.name} are set against`}
                  onClick={() => reference.mutate()}
                >
                  {reference.isSuccess ? 'The rule’s reference' : 'Set as the rule’s reference'}
                </Button>
              )}
              {/* Its script, inputs, alerts and bars made a signal rule, in shadow: reviewed in the rule's dialog first. */}
              {run.kind === 'run' && run.status.state === 'done' && run.request.source.kind === 'pine' && !run.stress_of && !run.model_check_of && (
                <ButtonLink to={`/ta/signals?from-run=${run.id}`} variant="outline" size="sm" leftIcon={<Radar className="size-3.5" />}>
                  Make a shadow rule
                </ButtonLink>
              )}
              <ButtonLink to={`/backtests/new?again=${run.id}`} size="sm" leftIcon={<RotateCcw className="size-3.5" />}>
                Run again with changes
              </ButtonLink>
            </>
          )
        }
      />
      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState isPending={runQuery.isPending} error={runQuery.error} hasData={!!run}>
          {run && (
            <div className="flex flex-col gap-6">
              <div className="flex flex-wrap items-center gap-2">
                <StateBadge status={run.status} />
                {run.kind === 'sweep' && <Badge tone="neutral">{run.stress_of ? 'Stress' : 'Sweep'}</Badge>}
                {run.turbos === 'modelled' && <Badge tone="warning">Modelled turbos</Badge>}
                {run.model_check_of && <Badge tone="neutral">Model check</Badge>}
                {run.stress_of && (
                  <Link to={`/backtests/${run.stress_of}`} className="text-sm font-medium underline-offset-4 hover:underline">
                    The run it stresses
                  </Link>
                )}
                {run.model_check_of && (
                  <Link to={`/backtests/${run.model_check_of}`} className="text-sm font-medium underline-offset-4 hover:underline">
                    The run it checks
                  </Link>
                )}
                {run.tags.map((tag) => (
                  <Badge key={tag} tone="neutral">
                    {tag}
                  </Badge>
                ))}
              </div>

              {reference.error && (
                <p role="alert" className="text-sm text-negative">
                  {reference.error.message}
                </p>
              )}
              {isActive(run.status.state) && <Progress run={run} />}
              {(run.status.state === 'failed' || run.status.state === 'interrupted') && (
                <Alert tone="danger" role="alert">
                  <AlertTitle>{run.status.state === 'failed' ? 'The backtest failed' : 'Interrupted'}</AlertTitle>
                  <AlertDescription>
                    <pre className="whitespace-pre-wrap font-mono text-xs">{run.status.error}</pre>
                  </AlertDescription>
                </Alert>
              )}
              {run.status.state === 'cancelled' && (
                <Alert tone="neutral">
                  <AlertDescription>Stopped before it was done: nothing of it was kept.</AlertDescription>
                </Alert>
              )}

              {parts.length > 0 && (
                <ScrollProgressNav mode="sticky" containerRef={scroller ?? undefined} label="Parts of this page" sections={parts} />
              )}

              {done && summary && run.kind === 'sweep' && (
                <>
                  <p className="text-sm text-muted-foreground">{daysSentence(summary.days)}</p>
                  <div id="verdict" className={ANCHORED}>
                    <VerdictPanel run={run} />
                  </div>
                  <div id="sweep" className={ANCHORED}>
                    <SweepPanel runId={run.id} fields={fields} stress={Boolean(run.stress_of)} />
                  </div>
                  {!run.stress_of && (
                    <div id="choice" className={ANCHORED}>
                      <ChoicePanel runId={run.id} summary={summary} fields={fields} />
                    </div>
                  )}
                </>
              )}

              {single && summary && figures && money && (
                <>
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
                    <KpiTile
                      label="All days"
                      value={<PnlValue value={figures.total_percent} kind="percent" />}
                      hint={figures.total_range ? `${formatPercent(figures.total_range.low, true)} to ${formatPercent(figures.total_range.high, true)} with other days` : 'each trade as the whole stake'}
                    />
                    <KpiTile
                      label="In money"
                      value={<PnlValue value={money.profit_loss} kind="money" />}
                      hint={`${formatMoney(money.capital, 'EUR')} to ${formatMoney(money.final, 'EUR')} (${formatPercent(money.return_percent, true)})`}
                    />
                    <KpiTile
                      label="Trades"
                      value={formatNumber(figures.trades)}
                      hint={`${formatPercent(figures.win_rate, false, 0)} won, on ${figures.days_with_an_entry} of ${figures.days} days`}
                    />
                    <KpiTile label="Per trade" value={<PnlValue value={figures.expectancy} kind="percent" />} hint={`won ${formatPercent(figures.average_win, true)}, lost ${formatPercent(figures.average_loss, true)}`} />
                    <KpiTile label="Worst drawdown" value={<PnlValue value={figures.worst_drawdown} kind="percent" />} hint={`${formatMoney(money.worst_drawdown, 'EUR')} of the account`} />
                    <KpiTile
                      label="Beats random"
                      value={summary.against_random ? formatPercent(summary.against_random.beaten, false, 0) : '-'}
                      hint={summary.against_random ? `of ${summary.against_random.repetitions} runs at random` : 'not asked'}
                    />
                  </div>
                  <p className="text-sm text-muted-foreground">
                    {daysSentence(summary.days)} Average stake {formatMoney(money.average_stake, 'EUR')}, {formatPercent(money.average_scale_percent, false, 0)} of
                    what an order may use{money.commissions ? `; ${formatMoney(money.commissions, 'EUR')} of commissions` : ''}
                    {money.unfunded ? `; ${money.unfunded} entr${money.unfunded === 1 ? 'y' : 'ies'} the account could not buy a turbo for` : ''}.
                  </p>
                  {/* What the run's trades and days are worth: said by the verdict's first check. */}
                  <div id="verdict" className={ANCHORED}>
                    <VerdictPanel run={run} />
                  </div>
                  {summary.warnings.length > 0 && (
                    <Alert tone="neutral">
                      <AlertTitle>What the Pine runner said</AlertTitle>
                      <AlertDescription>{summary.warnings.slice(0, 5).join(' · ')}</AlertDescription>
                    </Alert>
                  )}
                  <div id="trust" className={ANCHORED}>
                    <TrustPanel runId={run.id} summary={summary} />
                  </div>
                  {run.request.source.kind === 'journal' && (
                    <div id="reconciliation" className={ANCHORED}>
                      <ReconciliationPanel runId={run.id} />
                    </div>
                  )}
                  <div id="edge" className={ANCHORED}>
                    <EdgePanel runId={run.id} />
                  </div>
                  <div id="stress" className={ANCHORED}>
                    <StressPanel run={run} />
                  </div>
                  {hasModelCheck(run) && (
                    <div id="model-check" className={ANCHORED}>
                      <ModelCheckPanel run={run} />
                    </div>
                  )}
                  <div id="curves" className={ANCHORED}>
                    <CurvesPanel summary={summary} />
                  </div>
                  <div id="why" className={ANCHORED}>
                    <WhyPanel summary={summary} />
                  </div>
                  <div id="breakdowns" className={ANCHORED}>
                    <BreakdownsPanel runId={run.id} />
                  </div>
                  <Panel id="tables" className={ANCHORED}>
                    <AnimatedTabs
                      tabs={[
                        { id: 'trades', label: 'Trades', badge: tradesQuery.data?.items.length, href: tableHref('trades') },
                        { id: 'signals', label: 'Signals', badge: signalsQuery.data?.items.length, href: tableHref('signals') },
                        { id: 'days', label: 'Days', badge: daysQuery.data?.items.length, href: tableHref('days') },
                      ]}
                      activeTab={tab}
                      onChange={setTab}
                      linkComponent={RouterLink}
                      renderContent={false}
                    />
                    <div className="mt-4">
                      {tab === 'trades' && (
                        <QueryState isPending={tradesQuery.isPending} error={tradesQuery.error} hasData={!!tradesQuery.data}>
                          {tradesQuery.data && <TradesTable runId={run.id} trades={tradesQuery.data.items} timezone={timezone} />}
                        </QueryState>
                      )}
                      {tab === 'signals' && (
                        <QueryState isPending={signalsQuery.isPending} error={signalsQuery.error} hasData={!!signalsQuery.data}>
                          {signalsQuery.data && <SignalsTable runId={run.id} signals={signalsQuery.data.items} timezone={timezone} />}
                        </QueryState>
                      )}
                      {tab === 'days' && (
                        <QueryState isPending={daysQuery.isPending} error={daysQuery.error} hasData={!!daysQuery.data}>
                          {daysQuery.data && <DaysTable runId={run.id} days={daysQuery.data.items} turbos={run.turbos} />}
                        </QueryState>
                      )}
                    </div>
                  </Panel>
                </>
              )}

              <div id="setup" className={ANCHORED}>
                <RunFacts run={run} labels={fields} />
              </div>
            </div>
          )}
        </QueryState>
      )}

      <SettleModal
        open={removing}
        onClose={() => setRemoving(false)}
        title="Remove this backtest?"
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRemoving(false)}>
              Keep it
            </Button>
            <Button variant="destructive" size="sm" isLoading={remove.isPending} onClick={() => remove.mutate()}>
              Remove
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-secondary">Its signals, trades and days are deleted from the server. The recorded prices are kept.</p>
        {remove.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {remove.error.message}
          </p>
        )}
      </SettleModal>

      <SettleModal
        open={renaming}
        onClose={() => setRenaming(false)}
        title="Name and tags"
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRenaming(false)}>
              Cancel
            </Button>
            <Button size="sm" isLoading={rename.isPending} disabled={!name.trim()} onClick={() => rename.mutate()}>
              Save
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-text-secondary">Name</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-text-secondary">Tags, separated by commas</span>
            <Input value={tags} onChange={(event) => setTags(event.target.value)} />
          </label>
          {rename.error && (
            <p role="alert" className="text-sm text-negative">
              {rename.error.message}
            </p>
          )}
        </div>
      </SettleModal>
    </>
  )
}
