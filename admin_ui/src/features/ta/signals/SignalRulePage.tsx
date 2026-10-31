// A signal rule's own page (/ta/signals/<id>): what it runs, whether it is ready to go live, a day of it drawn as the
// engine ran it with its signals, its days on paper, the signals it journaled, them beside TradingView's, how the
// engine runs it, and what it ran over time; its mode, its edit and its deletion, as the Signal rules page has them
// (useRuleActions). It reads what the pages of before read (the rules, the engine, paper trading, the journal, the
// comparison) and two answers of its own: its history (GET /api/ta/rules/{id}/history) and a day of it
// (GET /api/ta/rules/{id}/days/{day}/indicator).

import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  ButtonLink,
  CheckList,
  DescriptionList,
  formatDateTime,
  formatDay,
  Hint,
  Input,
  InteractiveTimeline,
  lastDays,
  PageHeader,
  Panel,
  QueryState,
  rangeParams,
  ScrollProgressNav,
  Select,
  Skeleton,
  useMainScroll,
} from 'ioiti-ui'
import { ChartSpline, ChevronLeft, ChevronRight, Pencil, Trash2 } from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { ClaudeBadge } from '@/components/status'
import { AgentSheet } from '@/features/agent/AgentSheet'
import { AgentVerdict } from '@/features/agent/AgentVerdict'
import { AskAgentLink } from '@/features/agent/AskAgentLink'
import { useSession } from '@/features/auth/auth-context'
import { PaperDays, PaperReport, Reference } from '@/features/backtests/PaperParts'
import { paperTradingHref } from '@/features/backtests/paperModel'
import { usePaper } from '@/features/backtests/usePaper'
import { madeByClaude } from '@/features/system/claude'
import { monitorHref } from '@/features/ta/monitor/monitorModel'
import { api } from '@/lib/api'
import type { TaRule, TaRuleState } from '@/lib/types'

import { chartsPath } from '../chartLinks'
import { tickerText } from '../engine/tickers'
import { ComparisonPanel } from './ComparisonPanel'
import { DeleteRuleDialog } from './DeleteRuleDialog'
import { liveReadiness } from './readiness'
import { RuleDialog } from './RuleDialog'
import { RuleModeDialog } from './RuleModeDialog'
import { barsLabel, conflictText, ENGINE_STATE, MODE_LABEL, MODE_TONE, mappedAlerts, updatesOf } from './ruleModel'
import { firstDay, historyItems, liveSignalsSaid, openingDay, readinessSaid, ruleLayer, steps } from './rulePageModel'
import { SignalJournal } from './SignalJournal'
import { useRuleActions } from './useRuleActions'

// Vela only once a day of the rule is drawn.
const RunDayChart = lazy(() => import('@/features/ta/chart/RunDayChart').then((module) => ({ default: module.RunDayChart })))

// The sticky index of the page leaves a part's top in view.
const ANCHORED = 'scroll-mt-14'
const SECTIONS = [
  { id: 'going-live', label: 'Going live' },
  { id: 'day', label: 'Its day' },
  { id: 'paper', label: 'On paper' },
  { id: 'signals', label: 'Signals' },
  { id: 'tradingview', label: 'Beside TradingView' },
  { id: 'engine', label: 'Engine' },
  { id: 'history', label: 'History' },
].map((section, index) => ({ ...section, index: String(index + 1).padStart(2, '0'), href: `#${section.id}` }))
// A live rule's signals of its last days, said in a sentence.
const LIVE_DAYS = 7
const DAY_PARAM = 'day'

const RULE_NOT_FOUND = {
  title: 'No such signal rule',
  description: 'No signal rule has this address: it may have been deleted, or the address is mistyped.',
  suggestions: [{ label: 'Signal rules', href: '/ta/signals', description: 'The rules the TA engine runs' }],
}

/** The day drawn, the weekdays before and after it the page may step to, and a day picked. */
function DayStepper({ day, first, last, onDay }: { day: string; first: string; last: string; onDay: (day: string) => void }) {
  const { before, after } = steps(day, first, last)
  return (
    <div className="flex items-end gap-1">
      <Button variant="outline" size="icon" className="size-10" aria-label="The weekday before" disabled={!before}
        onClick={() => before && onDay(before)}>
        <ChevronLeft className="size-4" />
      </Button>
      <label className="flex flex-col gap-1">
        <span className="text-xs text-text-secondary">Day</span>
        <Input type="date" value={day} min={first} max={last} onChange={(event) => event.target.value && onDay(event.target.value)} />
      </label>
      <Button variant="outline" size="icon" className="size-10" aria-label="The weekday after" disabled={!after}
        onClick={() => after && onDay(after)}>
        <ChevronRight className="size-4" />
      </Button>
    </div>
  )
}

/** How the engine runs it: its state, its last bar and run, its last signal. */
function EngineFacts({ rule, state, timeZone }: { rule: TaRule; state: TaRuleState | undefined; timeZone: string }) {
  if (!state) {
    return (
      <p className="text-sm text-muted-foreground">
        {rule.mode === 'off' ? 'Off: the engine does not run it.' : 'Not seen yet: the engine takes it up at its next tick.'}
      </p>
    )
  }
  const shown = ENGINE_STATE[state.state] ?? { label: state.state, tone: 'neutral' as const }
  const figures = state.figures
  return (
    <DescriptionList
      items={[
        { term: 'State', details: <span className="inline-flex flex-wrap items-center gap-2"><Badge tone={shown.tone}>{shown.label}</Badge>{state.detail}</span> },
        { term: 'Its last bar', details: state.last_bar_time ? formatDateTime(state.last_bar_time, timeZone) : 'None yet' },
        {
          term: 'Its last run',
          details: figures.milliseconds != null
            ? `${figures.milliseconds} ms, ${figures.whole ? `run whole over all its bars: ${figures.whole}` : 'its run continued from the bar before'}`
            : 'Not measured yet',
        },
        { term: 'Its last signal', details: state.last_alert_at ? formatDateTime(state.last_alert_at, timeZone) : 'None yet' },
      ]}
    />
  )
}

export function SignalRulePage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { hash } = useLocation()
  const [params, setParams] = useSearchParams()
  const { timezone, actions_enabled: actionsEnabled, backtests_enabled: backtestsEnabled } = useSession()
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, refetchOnWindowFocus: false })
  const scriptsQuery = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts })
  const engineQuery = useQuery({ queryKey: ['ta-engine'], queryFn: api.taEngine, refetchInterval: 10_000 })
  const paperQuery = usePaper(backtestsEnabled)
  const rules = rulesQuery.data
  const rule = rules?.rules.find((entry) => entry.id === id)
  const state = engineQuery.data?.rules[id]
  const paper = paperQuery.data?.rules[id]
  // Read again each time the rule changes: what it runs, or its mode.
  const historyQuery = useQuery({
    queryKey: ['ta-rule-history', id, rule?.updated_at, rule?.mode_changed_at],
    queryFn: () => api.taRuleHistory(id),
    enabled: Boolean(rule),
  })
  const { changing, setChanging, deleting, setDeleting, askMode, askDelete, modeMutation, deleteMutation } = useRuleActions(
    rules?.revision,
    { onDeleted: () => navigate('/ta/signals') },
  )
  const [editing, setEditing] = useState(false)
  const scroller = useMainScroll()

  // The day drawn: the address's (?day=), else today, or the last weekday.
  const [now] = useState(() => Date.now())
  const first = rule ? firstDay(rule, timezone) : null
  const opening = rule ? openingDay(rule, now, timezone) : null
  const asked = params.get(DAY_PARAM)
  const day = asked && /^\d{4}-\d{2}-\d{2}$/.test(asked) ? asked : opening
  const dayQuery = useQuery({
    queryKey: ['ta-rule-day', id, day],
    queryFn: () => api.taRuleDay(id, day!),
    enabled: Boolean(rule && day),
    retry: false,
    staleTime: 60_000,
  })
  const layer = useMemo(() => ruleLayer(dayQuery.data?.signals ?? []), [dayQuery.data])
  const toDay = (next: string) =>
    setParams((before) => {
      const after = new URLSearchParams(before)
      after.set(DAY_PARAM, next)
      return after
    }, { replace: true })

  // A live rule's signals of its last days: the journal's own query, read once for both.
  const liveParams = { ...rangeParams(lastDays(LIVE_DAYS)), rule: id }
  const liveQuery = useQuery({
    queryKey: ['ta-signals', liveParams],
    queryFn: () => api.taSignals({ from: liveParams.from ?? undefined, to: liveParams.to ?? undefined, rule: id }),
    enabled: rule?.mode === 'live',
  })

  // An address that names a part of the page (#paper) is brought to it once the rule is drawn.
  const drawn = Boolean(rule)
  useEffect(() => {
    if (drawn && hash) document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.()
  }, [drawn, hash])

  if (rules && !rule) return <NotFoundState {...RULE_NOT_FOUND} />

  const checks = rule ? liveReadiness(rule, state, paper, now, timezone) : []
  const readiness = readinessSaid(checks)
  const engineShown = state ? (ENGINE_STATE[state.state] ?? { label: state.state, tone: 'neutral' as const }) : null
  const modeReadiness =
    changing?.mode === 'live' ? liveReadiness(changing.rule, engineQuery.data?.rules[changing.rule.id], paperQuery.data?.rules[changing.rule.id], now, timezone) : []
  const updates = rule ? updatesOf(rule) : null
  const mapped = rule ? mappedAlerts(rule) : []
  const items = historyQuery.data ? historyItems(historyQuery.data.events, timezone) : []
  const ticker = rule
    ? tickerText({ provider: 'capital', key: rule.asset.key, side: rule.side, rangeTicks: rule.bars.type === 'range' ? rule.bars.ticks : null })
    : null

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={rule?.name ?? 'Signal rule'}
        description={rule
          ? `${rule.script_name ?? 'A deleted script'} v${rule.script.version} on ${rule.asset.key} · ${barsLabel(rule.bars)} · signals for ${rule.indice}`
          : undefined}
        actions={rule && (
          <>
            <AskAgentLink subject={{ kind: 'rule', id: rule.id }} label="Ask the AI agent about it" />
            <Button variant="outline" size="sm" leftIcon={<Pencil className="size-3.5" />} onClick={() => setEditing(true)}>
              Edit
            </Button>
            {/* A live rule's Delete is disabled: its hint says why, reached through a wrapper that takes the focus. */}
            <Hint content={rule.mode === 'live' ? 'Switch it off before deleting it' : 'Delete it'} wrap={rule.mode === 'live'}>
              <Button variant="outline" size="sm" leftIcon={<Trash2 className="size-3.5" />} disabled={rule.mode === 'live'}
                onClick={() => askDelete(rule)}>
                Delete
              </Button>
            </Hint>
          </>
        )}
      />

      <QueryState isPending={rulesQuery.isPending} error={rulesQuery.error} hasData={Boolean(rule)}>
        {rule && rules && (
          <div className="flex flex-col gap-6">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={MODE_TONE[rule.mode]} dot={rule.mode === 'live'} pulse={rule.mode === 'live'}>
                {MODE_LABEL[rule.mode]}
              </Badge>
              {madeByClaude(rule) && <ClaudeBadge />}
              {engineShown && <Badge tone={engineShown.tone}>Engine: {engineShown.label.toLowerCase()}</Badge>}
              {rule.mode !== 'live' && <Badge tone={readiness.ready ? 'success' : 'neutral'}>{readiness.text}</Badge>}
              <div className="w-36">
                <Select
                  aria-label={`Mode of ${rule.name}`}
                  value={rule.mode}
                  onChange={(event) => askMode(rule, event.target.value as TaRule['mode'])}
                  options={(['off', 'shadow', 'live'] as const).map((mode) => ({
                    value: mode,
                    label: MODE_LABEL[mode],
                    disabled: mode === 'live' && rule.mode !== 'live' && !actionsEnabled,
                  }))}
                />
              </div>
            </div>
            {modeMutation.error && !changing && (
              <Alert tone="danger" role="alert">
                <AlertDescription>{conflictText(modeMutation.error)}</AlertDescription>
              </Alert>
            )}
            <AgentVerdict about={`rule:${rule.id}`} changedAt={rule.run_changed_at} />

            <Panel title="What it runs">
              <DescriptionList
                items={[
                  {
                    term: 'Its script',
                    details: (
                      <span>
                        <Link className="underline-offset-2 hover:underline" to={`/ta/indicators/${rule.script.id}`}>
                          {rule.script_name ?? 'a deleted script'}
                        </Link>{' '}
                        v{rule.script.version}
                        {updates?.script !== null && updates?.script !== undefined && (
                          <span className="text-muted-foreground"> (v{updates.script} is saved)</span>
                        )}
                      </span>
                    ),
                  },
                  {
                    term: 'Its libraries',
                    details: rule.libraries.length > 0 ? rule.libraries.map((entry) => `${entry.name} v${entry.version}`).join(', ') : 'None',
                  },
                  { term: 'Runs on', details: `${rule.asset.key}, its ${rule.side}, ${barsLabel(rule.bars)}` },
                  {
                    term: 'Its days of history',
                    details: rule.history_days === null || rule.history_days === undefined
                      ? `The engine’s, ${rules.engine_history_days} day${rules.engine_history_days === 1 ? '' : 's'}`
                      : `${rule.history_days} day${rule.history_days === 1 ? '' : 's'} of its own`,
                  },
                  { term: `Signals for ${rule.indice}`, details: mapped.length > 0 ? mapped.join('; ') : 'No alert becomes a signal' },
                  {
                    term: 'Made',
                    details: `${formatDateTime(rule.created_at, timezone)}${madeByClaude(rule) ? ', by Claude' : ''}`,
                  },
                  { term: `${MODE_LABEL[rule.mode]} since`, details: formatDateTime(rule.mode_changed_at, timezone) },
                ]}
              />
            </Panel>

            <ScrollProgressNav mode="sticky" containerRef={scroller ?? undefined} label="Parts of this page" sections={SECTIONS} />

            <Panel id="going-live" className={ANCHORED} title="Going live"
              description="What the server checks before it takes the rule live: going live stays your switch, with its confirmation.">
              {rule.mode === 'live' ? (
                <div className="space-y-2 text-sm">
                  <p>Live since {formatDateTime(rule.mode_changed_at, timezone)}: its signals go to the trader.</p>
                  <p className="text-muted-foreground">
                    {liveQuery.data ? liveSignalsSaid(liveQuery.data.items, LIVE_DAYS) : 'Its signals are being read.'}
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {rule.mode === 'off' && (
                    <p className="text-sm text-muted-foreground">
                      Off: the engine does not run it, and nothing of it is journaled. In shadow, its week begins.
                    </p>
                  )}
                  <CheckList checks={checks} label="Is it ready to go live?" />
                  <p className="text-xs text-muted-foreground">
                    Short of its readiness, only “Go live anyway” goes past, and the audit trail records what was missing.
                  </p>
                </div>
              )}
            </Panel>

            <Panel
              id="day"
              className={ANCHORED}
              title="Its day"
              description="Its script on the bars the engine ran it on that day, from its days of history before the engine’s restart, with the signals it raised. Nothing is run live, nothing is saved."
              action={ticker && (
                <Link
                  to={chartsPath({ symbol: ticker, tf: rule.bars.type === 'time' ? rule.bars.timeframe : '1', script: rule.script.id })}
                  className="inline-flex items-center gap-1.5 text-sm text-text-secondary underline-offset-4 hover:underline"
                >
                  <ChartSpline className="size-4" /> On the Charts page
                </Link>
              )}
            >
              {day === null || first === null || opening === null ? (
                <p className="text-sm text-muted-foreground">
                  Made {formatDateTime(rule.created_at, timezone)}: its first day is {first ? formatDay(first) : 'the next weekday'}.
                </p>
              ) : (
                <div className="space-y-3">
                  <DayStepper day={day} first={first} last={opening} onDay={toDay} />
                  <QueryState isPending={dayQuery.isPending} error={dayQuery.error} hasData={Boolean(dayQuery.data)}>
                    {dayQuery.data && (
                      <div className="space-y-2">
                        <Suspense fallback={<Skeleton className="h-[28rem] w-full" />}>
                          <RunDayChart
                            answer={dayQuery.data}
                            day={day}
                            signals={[]}
                            trades={[]}
                            selected={null}
                            timeZone={timezone}
                            label={`${rule.name} on ${formatDay(day)}`}
                            layer={layer}
                          />
                        </Suspense>
                        <p className="text-xs text-muted-foreground">
                          {dayQuery.data.signals.length === 0
                            ? 'No signal that engine day.'
                            : `${dayQuery.data.signals.length} signal${dayQuery.data.signals.length === 1 ? '' : 's'} that engine day, on the bar each was raised on.`}
                        </p>
                        {dayQuery.data.notes.length > 0 && (
                          <Alert tone="neutral">
                            <AlertDescription>{dayQuery.data.notes.join(' ')}</AlertDescription>
                          </Alert>
                        )}
                      </div>
                    )}
                  </QueryState>
                </div>
              )}
            </Panel>

            <Panel
              id="paper"
              className={ANCHORED}
              title="On paper"
              description="Each finished weekday, the signals it raised traded as WATA would have traded them, and its report since it was made."
              action={
                <ButtonLink to={paperTradingHref(rule.id)} variant="outline" size="sm">
                  Paper trading
                </ButtonLink>
              }
            >
              {!backtestsEnabled ? (
                <p className="text-sm text-muted-foreground">
                  The backtests are switched off (<code>admin.backtests_enabled</code>): nothing is traded on paper.
                </p>
              ) : (
                <QueryState isPending={paperQuery.isPending} error={paperQuery.error} hasData={Boolean(paperQuery.data)}>
                  <div className="flex flex-col gap-5">
                    <PaperReport owner={rule.id} name={rule.name} entry={paper} inShadow={rule.mode === 'shadow'} canAsk={rule.mode === 'shadow'} />
                    <section aria-label={`Days on paper of ${rule.name}`} className="space-y-2">
                      <h3 className="text-sm font-medium">Its days on paper</h3>
                      <Reference reference={paper?.reference ?? null} ruleId={rule.id} />
                      <PaperDays days={paper?.days ?? []} madeAgain={rule.mode === 'shadow'} />
                    </section>
                  </div>
                </QueryState>
              )}
            </Panel>

            <div id="signals" className={ANCHORED}>
              <SignalJournal rules={[rule]} timeZone={timezone} fixed />
            </div>

            <div id="tradingview" className={ANCHORED}>
              <ComparisonPanel rules={[rule]} timeZone={timezone} fixed />
            </div>

            <Panel
              id="engine"
              className={ANCHORED}
              title="Engine"
              description="How the TA engine runs it, as it last said."
              action={
                <ButtonLink to={monitorHref({ rule: rule.id })} variant="outline" size="sm">
                  Engine monitor
                </ButtonLink>
              }
            >
              <EngineFacts rule={rule} state={state} timeZone={timezone} />
            </Panel>

            <Panel
              id="history"
              className={ANCHORED}
              title="History"
              description="What it ran over time: its making, each change of what it runs, and its switches into and out of live, as the audit trail recorded them."
            >
              <QueryState isPending={historyQuery.isPending} error={historyQuery.error} hasData={Boolean(historyQuery.data)}>
                {items.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Nothing kept of it yet.</p>
                ) : (
                  <InteractiveTimeline
                    key={items.map((item) => item.id).join('|')}
                    items={items}
                    label={`The history of ${rule.name}`}
                    defaultExpandedIds={items.map((item) => item.id)}
                    className="max-w-[60rem]"
                  />
                )}
              </QueryState>
            </Panel>
          </div>
        )}
      </QueryState>

      {editing && rule && rules && (
        <RuleDialog open rule={rule} start="edit" rules={rules} scripts={scriptsQuery.data?.items ?? []} onClose={() => setEditing(false)} />
      )}
      <RuleModeDialog
        change={changing}
        revision={rules?.revision}
        readiness={modeReadiness}
        onClose={() => setChanging(null)}
        verdict={changing ? <AgentVerdict about={`rule:${changing.rule.id}`} changedAt={changing.rule.run_changed_at} /> : null}
      />
      <DeleteRuleDialog
        rule={deleting}
        pending={deleteMutation.isPending}
        error={deleteMutation.error}
        onCancel={() => setDeleting(null)}
        onDelete={(target) => deleteMutation.mutate(target)}
      />
      {rule && <AgentSheet subject={{ kind: 'rule', id: rule.id, label: rule.name }} />}
    </div>
  )
}
