import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Tabs,
  Badge,
  Button,
  ButtonLink,
  cn,
  formatDay,
  formatMoney,
  formatPercent,
  Input,
  PageHeader,
  Panel,
  QueryState,
  ScrollProgressNav,
  Dialog,
  SplitButton,
  useMainScroll,
  useToast,
} from 'ioiti-ui'
import type { SplitButtonOption } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { Flag, Moon, Pencil, Radar, RotateCcw, SlidersHorizontal, Sparkles, Square, Star, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { AgentSheet } from '@/features/agent/AgentSheet'
import { AgentVerdict } from '@/features/agent/AgentVerdict'
import { NEW, withAgent } from '@/features/agent/model'
import { useSession } from '@/features/auth/auth-context'
import { ApplyCard } from '@/features/config/ApplyCard'
import { useLuxAlgoStatus } from '@/features/ta/luxalgo/useLuxAlgo'
import { api, ApiError, isNotFound } from '@/lib/api'
import type { BacktestRun, BacktestRunListed, ConfigSaved } from '@/lib/types'

import {
  daysSentence,
  isActive,
  progressSentence,
  remakable,
  RUN_NOT_FOUND,
  sourceLabel,
  STATE_LABEL,
} from './model'
import { ApplyToLiveDialog } from './ApplyToLiveDialog'
import { configurationHref, configurationKey, replacedOf } from './configurations'
import { EdgePanel } from './EdgePanel'
import { BacktestsDisabled, StateBadge } from './parts'
import { useRemoveRuns } from './pending-removals-context'
import { useSetFavourites } from './useFavourites'
import { ReconciliationPanel } from './ReconciliationPanel'
import { BreakdownsPanel, CurvesPanel, RunKpis, TrustPanel, WhyPanel } from './runPanels'
import { DaysTable, SignalsTable, TradesTable } from './runTables'
import { ModelCheckPanel } from './ModelCheckPanel'
import { paperRunLine, runParents } from './paperModel'
import { choiceTrust } from './robustness'
import { PropFirmPanel } from './PropFirmPanel'
import { StressPanel } from './StressPanel'
import { AutopsyPanel } from './AutopsyPanel'
import { CapsPanel } from './CapsPanel'
import { ChoicePanel, SweepPanel } from './SweepPanel'
import { TagsField } from './TagsField'
import { RunFacts } from './RunFacts'
import { VerdictPanel } from './VerdictPanel'
import type { PageSection } from './VerdictPanel'
import { RunPeekLink } from './RunPeek'

/** Where a part of the page starts when the nav leads to it: below the nav, which stays at the top. */
const ANCHORED = 'scroll-mt-14'

/** The parts of a done run's page, in their order: what the index lists, each an anchor. */
function runSections(run: BacktestRun, luxalgo: boolean): PageSection[] {
  const summary = run.summary
  if (run.status.state !== 'done' || !summary) return []
  const judged = !run.stress_of && !run.noise_of && !run.model_check_of && !run.forward_of && !run.calibrated_of
  if (run.kind === 'sweep') {
    const choosing = !run.stress_of && !run.noise_of && (choiceTrust(summary).length > 0 || summary.walk_forward !== undefined)
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
    // A day on paper has neither: too few trades to test a cut, and no twin of its own.
    ...(judged && !run.paper ? [{ id: 'autopsy', label: 'Winners and losers' }, { id: 'caps', label: 'The day’s caps' }] : []),
    ...(luxalgo && judged ? [{ id: 'propfirms', label: 'Funded challenges' }] : []),
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
            <div className="h-1.5 rounded-full bg-foreground/60 transition-all accent:bg-brand" style={{ width: `${Math.max(share * 100, 3)}%` }} />
          </div>
        )}
        {run.lines.length > 0 && (
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs text-muted-foreground">{run.lines.join('\n')}</pre>
        )}
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
  const removeRuns = useRemoveRuns()
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const toast = useToast()
  // Its settings applied to WATA: the dialog, then what the save did, until dismissed.
  const [applying, setApplying] = useState(false)
  const [applied, setApplied] = useState<ConfigSaved | null>(null)
  const appliedCatalog = useQuery({ queryKey: ['config-catalog'], queryFn: api.configCatalog, enabled: !!applied, staleTime: Infinity })

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
  const reference = useMutation({
    mutationFn: () => api.setBacktestReference(runId),
    onSuccess: (paper) => queryClient.setQueryData(['backtest-paper'], paper),
  })
  const rename = useMutation({
    mutationFn: () => api.renameBacktest(runId, { name, tags }),
    onSuccess: () => {
      void refresh()
      setRenaming(false)
    },
  })
  // Among the favourites, or out of them: the list's filter keeps them.
  const { mutate: favour } = useSetFavourites()
  // Made again on today's code, one at a time once no other run waits: followed through the list of runs.
  const remake = useMutation({
    mutationFn: () => api.remakeBacktests({ runs: [runId] }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['backtests'] }),
  })
  const listQuery = useQuery({ queryKey: ['backtests'], queryFn: api.backtests, enabled })
  // The run making this one again, while it waits or runs.
  const remaking = listQuery.data?.items.find((other) => other.remade_from === runId && isActive(other.status.state)) ?? null

  // The configuration it is of (the runs of the same things but their days): its name, its page.
  const configurationId = run && !run.paper ? (run.configuration ?? null) : null
  const configurationQuery = useQuery({
    queryKey: configurationKey(configurationId ?? ''),
    queryFn: () => api.backtestConfiguration(configurationId ?? ''),
    enabled: enabled && !!configurationId,
    staleTime: 60_000,
  })
  const configuration = configurationQuery.data?.configuration ?? null

  const summary = run?.summary
  const figures = summary?.figures
  const money = summary?.money
  const paperLine = run ? paperRunLine(run) : null
  // LuxAlgo's prop-firm simulator, while the admin reads LuxAlgo (admin.luxalgo_enabled).
  const luxalgo = !!useLuxAlgoStatus().data?.enabled
  const sections = useMemo(() => (run ? runSections(run, luxalgo) : []), [run, luxalgo])
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

  if (isNotFound(runQuery.error)) {
    // A configuration's nightly run, replaced by the next: its address leads to the configuration.
    const replaced = runQuery.error instanceof ApiError ? replacedOf(runQuery.error.body) : null
    if (replaced) {
      return (
        <NotFoundState
          title="Replaced by a newer nightly run"
          description={`This run of ${replaced.name ?? 'a configuration'}${replaced.to ? `, to ${formatDay(replaced.to)}` : ''}, was made again since, over more days: its configuration shows the newest.`}
          suggestions={[
            { label: replaced.name ?? 'Its configuration', href: configurationHref(replaced.configuration), description: 'Its newest result and every run of it' },
            ...RUN_NOT_FOUND.suggestions,
          ]}
        />
      )
    }
    return <NotFoundState {...RUN_NOT_FOUND} />
  }

  // What else can be done with the run, behind the chevron of "Run again with changes".
  const runActions = (shown: BacktestRun): SplitButtonOption[] => {
    const done = shown.kind === 'run' && shown.status.state === 'done'
    const options: SplitButtonOption[] = []
    // Its script, inputs, alerts and bars made a signal rule, in shadow: reviewed in the rule's dialog first.
    if (done && shown.request.source.kind === 'pine' && !shown.stress_of && !shown.model_check_of) {
      options.push({ value: 'rule', label: 'Make a shadow rule', icon: <Radar />, href: `/ta/signals?from-run=${shown.id}` })
    }
    if (done && shown.source.from_rule && shown.source.kind === 'pine') {
      options.push({
        value: 'reference',
        label: reference.isSuccess ? 'The rule’s reference' : 'Set as the rule’s reference',
        description: `The run the paper days of ${shown.source.from_rule.name} are set against`,
        icon: <Flag />,
        disabled: reference.isSuccess || reference.isPending,
        onSelect: () => reference.mutate(),
      })
    }
    // A replay of the signals WATA received (not a paper day of its own): the live settings' reference.
    if (done && shown.source.kind === 'journal' && !shown.paper) {
      options.push({
        value: 'live-reference',
        label: reference.isSuccess ? 'The live reference' : 'Set as the live reference',
        description: 'The run the paper days of the signals received, and WATA’s real days, are set against',
        icon: <Flag />,
        disabled: reference.isSuccess || reference.isPending,
        onSelect: () => reference.mutate(),
      })
    }
    // Its configuration made again after each trading day: switched on from the configuration's page.
    if (shown.configuration && !shown.paper && shown.kind === 'run') {
      options.push({
        value: 'nightly',
        label: 'Make it again each evening…',
        description: 'Its configuration, its days stretched to the day just over after each trading day',
        icon: <Moon />,
        href: `${configurationHref(shown.configuration)}?nightly=open`,
      })
    }
    options.push({
      value: 'rename',
      label: 'Rename',
      icon: <Pencil />,
      onSelect: () => {
        setName(shown.name)
        setTags(shown.tags)
        setRenaming(true)
      },
    })
    // A paper report is WATA's to keep: made again after each finished day, the older removed.
    if (!isActive(shown.status.state) && !shown.paper?.report) {
      options.push({
        value: 'remove',
        label: 'Remove',
        description: 'Gone at once, with a few seconds to undo it',
        icon: <Trash2 />,
        tone: 'danger',
        onSelect: () => {
          removeRuns([{ id: shown.id, name: shown.name }])
          navigate(shown.paper ? '/backtests/paper' : '/backtests')
        },
      })
    }
    return options
  }

  return (
    <>
      <PageHeader
        title={run?.name ?? 'Backtest'}
        description={run ? `${sourceLabel(run)} · ${formatDay(run.from)} to ${formatDay(run.to)}` : undefined}
        parents={[...runParents(run), ...(configuration ? [{ label: configuration.name, href: configurationHref(configuration.id) }] : [])]}
        actions={
          run && (
            <>
              {/* A paper run is kept by paper trading, not in the list of runs: not one of the favourites. */}
              {!run.paper && (
                <Button
                  size="sm"
                  variant="outline"
                  aria-pressed={Boolean(run.favourite)}
                  leftIcon={<Star className={cn('size-3.5', run.favourite && 'fill-current text-warning')} aria-hidden />}
                  onClick={() => favour({ ids: [run.id], favourite: !run.favourite })}
                >
                  {run.favourite ? 'Remove from favourites' : 'Add to favourites'}
                </Button>
              )}
              <ButtonLink href={withAgent(params, NEW)} size="sm" variant="outline" leftIcon={<Sparkles className="size-3.5" />}>
                Ask the AI agent
              </ButtonLink>
              {/* A finished run of one combination, not a day on paper: its settings may become WATA's. */}
              {single && !run.paper && (
                <Button size="sm" variant="outline" leftIcon={<SlidersHorizontal className="size-3.5" />} onClick={() => setApplying(true)}>
                  Apply to WATA
                </Button>
              )}
              {/* Urgent, so never in a menu. */}
              {isActive(run.status.state) && (
                <Button variant="outline" size="sm" leftIcon={<Square className="size-3.5" />} pending={cancel.isPending} onClick={() => cancel.mutate()}>
                  Stop
                </Button>
              )}
              <SplitButton
                size="sm"
                variant="primary"
                label="Run again with changes"
                href={`/backtests/new?again=${run.id}`}
                linkComponent={RouterLink}
                icon={<RotateCcw className="size-3.5" aria-hidden />}
                menuLabel="More actions on this run"
                options={runActions(run)}
              />
            </>
          )
        }
      />
      {run && <AgentSheet subject={{ kind: 'run', id: run.id, label: run.name }} />}
      {run && single && !run.paper && (
        <ApplyToLiveDialog
          runId={run.id}
          open={applying}
          onClose={() => setApplying(false)}
          onSaved={(saved) => {
            setApplying(false)
            setApplied(saved)
            toast({
              title: 'Saved in config.json',
              description: `Previous version kept as ${saved.backup}. Each service named applies it at its next start.`,
              tone: 'success',
            })
            // The Configuration page holds the file's revision: it must read the new one, or its next save is refused.
            for (const key of ['config', 'config-backups', 'config-history', 'session', 'backtest-options', 'backtest-to-live']) {
              void queryClient.invalidateQueries({ queryKey: [key] })
            }
          }}
        />
      )}
      {applied && appliedCatalog.data && (
        <div className="mb-4">
          <ApplyCard catalog={appliedCatalog.data} saved={applied} onDismiss={() => setApplied(null)} />
        </div>
      )}
      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState isPending={runQuery.isPending} error={runQuery.error} hasData={!!run}>
          {run && (
            <div className="flex flex-col gap-6">
              <div className="flex flex-wrap items-center gap-2">
                <StateBadge status={run.status} />
                {run.kind === 'sweep' && <Badge tone="neutral">{run.stress_of ? 'Stress' : run.noise_of ? 'Fill noise' : 'Sweep'}</Badge>}
                {run.turbos === 'modelled' && <Badge tone="warning">Modelled turbos</Badge>}
                {run.model_check_of && <Badge tone="neutral">Model check</Badge>}
                {run.stress_of && (
                  <RunPeekLink id={run.stress_of} className="text-sm font-medium underline-offset-4 hover:underline">
                    The run it stresses
                  </RunPeekLink>
                )}
                {run.noise_of && (
                  <RunPeekLink id={run.noise_of} className="text-sm font-medium underline-offset-4 hover:underline">
                    The run whose fills it moves
                  </RunPeekLink>
                )}
                {run.model_check_of && (
                  <RunPeekLink id={run.model_check_of} className="text-sm font-medium underline-offset-4 hover:underline">
                    The run it checks
                  </RunPeekLink>
                )}
                {run.forward_of && <Badge tone="neutral">Forward check</Badge>}
                {run.forward_of && (
                  <RunPeekLink id={run.forward_of} className="text-sm font-medium underline-offset-4 hover:underline">
                    The candidate it judges on later days
                  </RunPeekLink>
                )}
                {run.calibrated_of && <Badge tone="neutral">Calibrated on live</Badge>}
                {run.calibrated_of && (
                  <RunPeekLink id={run.calibrated_of} className="text-sm font-medium underline-offset-4 hover:underline">
                    The run it makes again with what live measured
                  </RunPeekLink>
                )}
                {run.calibrated && (
                  <Link to={`/ta/signals/${run.calibrated.rule}#live-check`} className="text-sm font-medium underline-offset-4 hover:underline">
                    Its rule’s live check
                  </Link>
                )}
                {run.forward && (
                  <Link
                    to={`/backtests/experiments/forward/${run.forward.check}`}
                    className="text-sm font-medium underline-offset-4 hover:underline"
                  >
                    Its forward check
                  </Link>
                )}
                {run.made_via?.startsWith('agent:') && (
                  <Link to={`/agent/${encodeURIComponent(run.made_via.slice('agent:'.length))}`}
                    className="text-sm font-medium underline-offset-4 hover:underline">
                    Started by the AI agent
                  </Link>
                )}
                {run.tags.map((tag) => (
                  <Link key={tag} to={`/backtests?tag=${encodeURIComponent(tag)}`} aria-label={`The runs tagged ${tag}`} className="rounded-full">
                    <Badge tone="neutral">{tag}</Badge>
                  </Link>
                ))}
              </div>
              {configuration && (
                <p className="text-sm text-muted-foreground">
                  {run.nightly
                    ? 'A nightly run of the configuration '
                    : 'Of the configuration '}
                  <Link to={configurationHref(configuration.id)} className="font-medium text-foreground underline-offset-4 hover:underline">
                    {configuration.name}
                  </Link>
                  {` (${configuration.count} run${configuration.count === 1 ? '' : 's'})`}
                  {run.nightly
                    ? ': the next replaces it once done, unless something rests on it.'
                    : configuration.nightly?.on
                      ? ', made again each evening.'
                      : '.'}
                </p>
              )}
              <AgentVerdict about={`run:${run.id}`} />
              <RemadeLine run={run} remaking={remaking} asking={remake} />

              {paperLine && (
                <p className="text-sm text-muted-foreground">
                  {paperLine.text}{' '}
                  <Link to={paperLine.link.href} className="font-medium text-foreground underline-offset-4 hover:underline">
                    {paperLine.link.label}
                  </Link>
                </p>
              )}

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
                    <SweepPanel runId={run.id} fields={fields} stress={Boolean(run.stress_of)} noise={run.noise_of ? (summary.noise ?? []) : undefined} />
                  </div>
                  {!run.stress_of && !run.noise_of && (
                    <div id="choice" className={ANCHORED}>
                      <ChoicePanel runId={run.id} summary={summary} fields={fields} />
                    </div>
                  )}
                </>
              )}

              {single && summary && figures && money && (
                <>
                  <RunKpis summary={summary} />
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
                  {!run.stress_of && !run.noise_of && !run.model_check_of && !run.forward_of && !run.calibrated_of && !run.paper && (
                    <>
                      <div id="autopsy" className={ANCHORED}>
                        <AutopsyPanel runId={run.id} runName={run.name} family={run.research?.family?.name ?? null} />
                      </div>
                      <div id="caps" className={ANCHORED}>
                        <CapsPanel runId={run.id} />
                      </div>
                    </>
                  )}
                  {luxalgo && !run.stress_of && !run.model_check_of && (
                    <div id="propfirms" className={ANCHORED}>
                      <PropFirmPanel run={run} />
                    </div>
                  )}
                  <Panel id="tables" className={ANCHORED}>
                    <Tabs
                      tabs={[
                        { id: 'trades', label: 'Trades', badge: tradesQuery.data?.items.length, href: tableHref('trades') },
                        { id: 'signals', label: 'Signals', badge: signalsQuery.data?.items.length, href: tableHref('signals') },
                        { id: 'days', label: 'Days', badge: daysQuery.data?.items.length, href: tableHref('days') },
                      ]}
                      value={tab}
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

      <Dialog
        open={renaming}
        onClose={() => setRenaming(false)}
        title="Name and tags"
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRenaming(false)}>
              Cancel
            </Button>
            <Button size="sm" pending={rename.isPending} disabled={!name.trim()} onClick={() => rename.mutate()}>
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
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-secondary">Tags</span>
            <TagsField value={tags} onChange={setTags} limits={optionsQuery.data?.limits} />
          </div>
          {rename.error && (
            <p role="alert" className="text-sm text-negative">
              {rename.error.message}
            </p>
          )}
        </div>
      </Dialog>
    </>
  )
}

/**
 * Where a run stands against today's code: made again (the run that did, or the
 * one it makes again), being made again, or made with older code and offered to
 * be made again, which the list of runs then follows.
 */
function RemadeLine({
  run,
  remaking,
  asking,
}: {
  run: BacktestRun
  remaking: BacktestRunListed | null
  asking: { mutate: () => void; isPending: boolean; error: Error | null }
}) {
  const link = (id: string, label: string) => (
    <RunPeekLink id={id} className="font-medium text-foreground underline-offset-4 hover:underline">
      {label}
    </RunPeekLink>
  )
  if (run.remade_by) {
    return (
      <p className="text-sm text-muted-foreground">
        Made again on today’s code: {link(run.remade_by, 'the run that made it again')}. What this page shows is what it found on
        its older code.
      </p>
    )
  }
  return (
    <>
      {run.remade_from && (
        <p className="text-sm text-muted-foreground">
          Made again on today’s code from {link(run.remade_from, 'the run before')}, with its settings, script and signals.
        </p>
      )}
      {remaking ? (
        <p className="text-sm text-muted-foreground">
          Being made again on today’s code: {link(remaking.id, `its new run ${STATE_LABEL[remaking.status.state].toLowerCase()}`)}.
        </p>
      ) : (
        remakable(run) && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span>Made with older code than today’s: what it found may come out otherwise now.</span>
            <Button variant="outline" size="sm" leftIcon={<RotateCcw className="size-3.5" />} pending={asking.isPending} onClick={() => asking.mutate()}>
              Make it again
            </Button>
            {asking.error && (
              <span role="alert" className="text-negative">
                {asking.error.message}
              </span>
            )}
          </div>
        )
      )}
    </>
  )
}
