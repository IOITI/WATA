// Technical Analysis › Signal rules: WATA's own signals. A rule runs a script on
// the bars of an asset of the Capital.com feed, and maps its alerts to signals:
// journaled only in shadow, sent to the trader once the rule is live. The TA
// engine (src/ta_engine) runs them; this page makes and switches them, and shows
// what the engine did.

import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  Alert,
  AlertDescription,
  Badge,
  Button,
  ButtonLink,
  formatDateTime,
  Hint,
  PageHeader,
  Panel,
  QueryState,
  Select,
  SettleModal,
  StatusBadge,
  useToast,
} from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { Eye, Pencil, Plus, PowerOff, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { ClaudeBadge } from '@/components/status'
import { AgentSheet } from '@/features/agent/AgentSheet'
import { AgentVerdict, VerdictLine } from '@/features/agent/AgentVerdict'
import { AskAgentLink } from '@/features/agent/AskAgentLink'
import { ABOUT_PARAM, subjectOf } from '@/features/agent/model'
import { useVerdicts } from '@/features/agent/useConversation'
import { useSession } from '@/features/auth/auth-context'
import { paperReportHref, paperTradingHref } from '@/features/backtests/paperModel'
import { madeByClaude } from '@/features/system/claude'
import { monitorHref } from '@/features/ta/monitor/monitorModel'
import { api } from '@/lib/api'
import type { TaEngine, TaRule, TaRuleMode, TaRules } from '@/lib/types'

import { ComparisonPanel } from './ComparisonPanel'
import { DeleteRuleDialog } from './DeleteRuleDialog'
import { RuleDialog } from './RuleDialog'
import type { RuleDialogStart } from './RuleDialog'
import { barsLabel, conflictText, ENGINE_STATE, MODE_LABEL, MODE_TONE, mappedAlerts, newDraft, ruleDraftOfRun, rulePath, updatesOf } from './ruleModel'
import type { RuleDraft } from './ruleModel'
import { paperLine } from './paper'
import { liveReadiness } from './readiness'
import { RuleModeDialog } from './RuleModeDialog'
import { SignalJournal } from './SignalJournal'
import { useRuleActions } from './useRuleActions'

function EnginePanel({ engine, timeZone }: { engine: TaEngine; timeZone: string }) {
  const figures = (engine.engine?.figures ?? {}) as { live?: number; shadow?: number; covered_until?: number | null; runner?: { pinets?: string; node?: string } }
  return (
    <Panel
      title="TA engine"
      description="The service that runs the rules on each bar of the Capital.com feed."
      action={
        <ButtonLink to={monitorHref({})} variant="outline" size="sm">
          Engine monitor
        </ButtonLink>
      }
    >
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <StatusBadge status={engine.status} />
        <span>{engine.detail}</span>
      </div>
      {!engine.enabled && (
        <p className="mt-2 text-xs text-muted-foreground">
          It runs once <code>trade.config.ta_engine.enabled</code> is true (<Link className="underline" to="/config">Configuration</Link>)
          and <code>ta</code> is in <code>COMPOSE_PROFILES</code>. Rules can be made before: they wait for it.
        </p>
      )}
      {engine.engine && (
        <dl className="mt-3 grid gap-x-6 gap-y-1 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="inline">Rules on: </dt>
            <dd className="inline">{figures.live ?? 0} live, {figures.shadow ?? 0} in shadow</dd>
          </div>
          <div>
            <dt className="inline">Bars up to: </dt>
            <dd className="inline">
              {figures.covered_until ? formatDateTime(new Date(figures.covered_until * 1000).toISOString(), timeZone) : 'none yet'}
            </dd>
          </div>
          <div>
            <dt className="inline">Said at: </dt>
            <dd className="inline">{formatDateTime(engine.engine.updated_at, timeZone)}</dd>
          </div>
          <div>
            <dt className="inline">Runs on: </dt>
            <dd className="inline">
              PineTS {figures.runner?.pinets ?? '?'}, Node {figures.runner?.node ?? '?'}
            </dd>
          </div>
        </dl>
      )}
    </Panel>
  )
}

/** What the selection basket does to the rules selected: never live, which is one rule at a time. */
type BulkAction = 'off' | 'shadow' | 'delete'

interface BulkOutcome {
  action: BulkAction
  /** Rules changed or deleted, and those asked. */
  done: number
  asked: number
  /** What stopped it before the end, if anything did. */
  error: Error | null
}

/** A live rule is switched or deleted alone, with its confirmation: it has no box. */
const selectableRule = (rule: TaRule) => rule.mode !== 'live'
const ruleName = (rule: TaRule) => rule.name

const BULK_DONE: Record<BulkAction, { verb: string; description: string }> = {
  off: { verb: 'switched off', description: 'The engine stops running them.' },
  shadow: { verb: 'switched to shadow', description: 'Their signals are journaled, never sent.' },
  delete: { verb: 'deleted', description: 'Their signals stay in the journal; their scripts stay on the Indicators page.' },
}

function rulesCounted(count: number): string {
  return `${count} rule${count === 1 ? '' : 's'}`
}

export function SignalRulesPage() {
  const { timezone, actions_enabled: actionsEnabled, backtests_enabled: backtestsEnabled } = useSession()
  const toast = useToast()
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, refetchOnWindowFocus: false })
  const scriptsQuery = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts })
  const engineQuery = useQuery({ queryKey: ['ta-engine'], queryFn: api.taEngine, refetchInterval: 10_000 })
  // Each shadow rule's finished days traded on paper, by the backtests.
  const paperQuery = useQuery({ queryKey: ['backtest-paper'], queryFn: api.backtestPaper, enabled: backtestsEnabled, staleTime: 60_000 })
  const paper = paperQuery.data
  const rules: TaRules | undefined = rulesQuery.data
  // What the AI agent last concluded on each rule (its go-live dossier, say).
  const verdictsQuery = useVerdicts((rules?.rules ?? []).map((rule) => `rule:${rule.id}`))
  const verdicts = verdictsQuery.data?.items
  const scripts = useMemo(() => scriptsQuery.data?.items ?? [], [scriptsQuery.data])
  const states = engineQuery.data?.rules ?? {}

  const [dialog, setDialog] = useState<{ rule: TaRule | null; start: RuleDialogStart } | null>(null)

  // A rule asked for from elsewhere: from a script's page (?new=<script>), or made from a backtest
  // (?from-run=<run>). Its dialog opens once what it is made of is read, and the address forgets it as it closes.
  const [params, setParams] = useSearchParams()
  const askedScript = params.get('new')
  const askedRun = params.get('from-run')
  const fromRunQuery = useQuery({
    queryKey: ['backtest', askedRun],
    queryFn: () => api.backtest(askedRun ?? ''),
    enabled: Boolean(askedRun) && backtestsEnabled,
  })
  // An asset's tick, for a range read on another asset than the rule's.
  const assetsQuery = useQuery({ queryKey: ['data-assets', 'all'], queryFn: () => api.dataAssets('all'), enabled: Boolean(askedRun), staleTime: 60_000 })
  const askedDraft = useMemo((): RuleDraft | null => {
    if (!rules) return null
    if (askedScript) {
      const script = scripts.find((entry) => entry.id === askedScript && entry.kind !== 'library')
      return script ? newDraft(script, rules.instruments[0], rules.indices) : null
    }
    if (askedRun && fromRunQuery.data && assetsQuery.data) {
      const ticks = new Map(assetsQuery.data.items.map((asset) => [asset.ref, asset.tick]))
      return ruleDraftOfRun(fromRunQuery.data, rules.instruments, (ref) => ticks.get(ref))
    }
    return null
  }, [rules, scripts, askedScript, askedRun, fromRunQuery.data, assetsQuery.data])
  const shownDialog = dialog ?? (askedDraft ? { rule: null, start: 'edit' as const, draft: askedDraft } : null)
  // A rule asked about from its row: named by its name before the agent names it.
  const about = subjectOf(params.get(ABOUT_PARAM))
  const aboutRule = about?.kind === 'rule' ? rules?.rules.find((rule) => rule.id === about.id) : undefined
  const closeDialog = () => {
    setDialog(null)
    if (askedScript || askedRun) setParams({}, { replace: true })
  }
  const { changing, setChanging, deleting, setDeleting, askMode, askDelete, modeMutation, deleteMutation, refresh } =
    useRuleActions(rules?.revision)
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  // The rules the deletion of several is about, while it is asked.
  const [deletingMany, setDeletingMany] = useState<TaRule[] | null>(null)

  // A rule deleted meanwhile, or gone live, is no longer selected.
  const chosen = useMemo(() => (rules?.rules ?? []).filter((rule) => selected.has(rule.id) && selectableRule(rule)), [rules, selected])
  const chosenIds = useMemo(() => new Set(chosen.map((rule) => rule.id)), [chosen])
  /**
   * The rules chosen, one after the other, each with the revision the last change left (a deletion answers none:
   * the rules are read again). Stops at the first refusal, and says how far it went.
   */
  const bulkMutation = useMutation({
    mutationFn: async ({ action, targets }: { action: BulkAction; targets: TaRule[] }): Promise<BulkOutcome> => {
      let revision = rules!.revision
      let done = 0
      try {
        for (const [index, rule] of targets.entries()) {
          if (action === 'delete') {
            await api.deleteTaRule(rule.id, revision)
            done += 1
            if (index < targets.length - 1) revision = (await api.taRules()).revision
          } else if (rule.mode !== action) {
            revision = (await api.setTaRuleMode(rule.id, action, revision)).revision
            done += 1
          }
        }
        return { action, done, asked: targets.length, error: null }
      } catch (error) {
        return { action, done, asked: targets.length, error: error instanceof Error ? error : new Error(String(error)) }
      }
    },
    onSuccess: async (outcome, { targets }) => {
      if (outcome.error && outcome.action === 'delete') {
        // The dialog stays, on what is left, with why.
        setDeletingMany(targets.slice(outcome.done))
      } else {
        setDeletingMany(null)
      }
      if (!outcome.error) {
        setSelected(new Set())
        toast({ title: `${rulesCounted(outcome.done)} ${BULK_DONE[outcome.action].verb}`, description: BULK_DONE[outcome.action].description, variant: 'success' })
      }
      await refresh()
    },
  })
  const bulkError = bulkMutation.data?.error
    ? `Stopped after ${bulkMutation.data.done} of ${bulkMutation.data.asked}: ${conflictText(bulkMutation.data.error)}`
    : null
  const allIn = (mode: TaRuleMode) => chosen.length > 0 && chosen.every((rule) => rule.mode === mode)

  const columns = useMemo<ColumnDef<TaRule>[]>(
    () => [
      {
        id: 'name',
        header: 'Rule',
        accessorFn: (row) => `${row.name} ${row.script_name ?? ''}`,
        sortable: true,
        cell: ({ row }) => {
          const updates = updatesOf(row)
          return (
            <div className="min-w-0 max-w-80 whitespace-normal">
              <Link className="font-medium underline-offset-2 hover:underline" to={rulePath(row.id)}>
                {row.name}
              </Link>
              {madeByClaude(row) && <ClaudeBadge className="ml-2" />}
              <p className="text-xs text-muted-foreground">
                <Link className="underline-offset-2 hover:underline" to={`/ta/indicators/${row.script.id}`}>
                  {row.script_name ?? 'deleted script'}
                </Link>{' '}
                v{row.script.version}
                {row.libraries.length > 0 && ` · ${row.libraries.map((entry) => `${entry.name} v${entry.version}`).join(', ')}`}
              </p>
              {updates.script !== null && (
                <button type="button" className="text-xs underline underline-offset-2" onClick={() => setDialog({ rule: row, start: 'update-script' })}>
                  Run v{updates.script}
                </button>
              )}
              {updates.script === null && updates.libraries.length > 0 && (
                <button type="button" className="text-xs underline underline-offset-2" onClick={() => setDialog({ rule: row, start: 'update-libraries' })}>
                  Run {updates.libraries.map((entry) => `${entry.name} v${entry.to}`).join(', ')}
                </button>
              )}
              {verdicts?.[`rule:${row.id}`] && (
                <VerdictLine verdict={verdicts[`rule:${row.id}`]} changedAt={row.run_changed_at} className="mt-1 text-xs" />
              )}
            </div>
          )
        },
      },
      {
        id: 'asset',
        header: 'Runs on',
        accessorFn: (row) => `${row.asset.key} ${barsLabel(row.bars)}`,
        sortable: true,
        cell: ({ row }) => (
          <div className="whitespace-nowrap">
            {row.asset.key} · {row.side}
            <p className="text-xs text-muted-foreground">{barsLabel(row.bars)}</p>
          </div>
        ),
      },
      {
        id: 'signals',
        header: 'Signals',
        accessorFn: (row) => row.indice,
        cell: ({ row }) => {
          const mapped = mappedAlerts(row)
          return (
            <div className="min-w-0 max-w-72 whitespace-normal text-xs">
              <span className="font-medium">{row.indice}</span>
              <p className="text-muted-foreground">{mapped.length > 0 ? mapped.join('; ') : 'No alert becomes a signal'}</p>
            </div>
          )
        },
      },
      {
        id: 'mode',
        header: 'Mode',
        accessorKey: 'mode',
        sortable: true,
        filterable: true,
        filterOptions: (['off', 'shadow', 'live'] as const).map((mode) => ({ value: mode, label: MODE_LABEL[mode] })),
        cell: ({ row }) => (
          <div className="flex w-32 flex-col gap-1">
            <Badge tone={MODE_TONE[row.mode]} dot={row.mode === 'live'} pulse={row.mode === 'live'}>
              {MODE_LABEL[row.mode]}
            </Badge>
            <Select
              aria-label={`Mode of ${row.name}`}
              value={row.mode}
              onChange={(event) => askMode(row, event.target.value as TaRuleMode)}
              options={(['off', 'shadow', 'live'] as const).map((mode) => ({
                value: mode,
                label: MODE_LABEL[mode],
                disabled: mode === 'live' && row.mode !== 'live' && !actionsEnabled,
              }))}
            />
          </div>
        ),
      },
      {
        id: 'state',
        header: 'Engine',
        accessorFn: (row) => states[row.id]?.state ?? '',
        cell: ({ row }) => {
          const state = states[row.id]
          if (!state) return <span className="text-xs text-muted-foreground">{row.mode === 'off' ? 'Off' : 'Not seen yet'}</span>
          const shown = ENGINE_STATE[state.state] ?? { label: state.state, tone: 'neutral' as const }
          return (
            <div className="min-w-0 max-w-72 whitespace-normal text-xs">
              <Badge tone={shown.tone}>{shown.label}</Badge>
              {state.detail && <p className="mt-1 text-muted-foreground">{state.detail}</p>}
              {state.last_bar_time && (
                <p className="text-muted-foreground">
                  Last bar {formatDateTime(state.last_bar_time, timezone, false)}
                  {state.figures.milliseconds != null && (
                    <>
                      ,{' '}
                      <Link className="underline-offset-2 hover:underline" to={monitorHref({ rule: row.id })}>
                        run in {state.figures.milliseconds} ms
                      </Link>
                    </>
                  )}
                </p>
              )}
              {state.last_alert_at && <p className="text-muted-foreground">Last signal {formatDateTime(state.last_alert_at, timezone, false)}</p>}
              {state.figures.whole && <p className="text-muted-foreground">Each new bar runs it whole: {state.figures.whole}.</p>}
            </div>
          )
        },
      },
      {
        id: 'paper',
        header: 'On paper',
        cell: ({ row }) => {
          const entry = paper?.rules[row.id]
          const line = paperLine(entry, row.mode)
          if (!line) return <span className="text-xs text-muted-foreground">-</span>
          const last = entry?.days.find((day) => day.day_percent !== null)
          return (
            <div className="min-w-0 max-w-72 whitespace-normal text-xs">
              {/* Its report, one run of every day since it was made, in place of its days added up. */}
              {line.report ? (
                <Link className="font-medium underline-offset-2 hover:underline" to={paperReportHref(row.id)}>
                  {line.report}
                </Link>
              ) : (
                <span className="font-medium">{line.total}</span>
              )}
              {line.last && (
                <p className="text-muted-foreground">
                  {last ? (
                    <Link className="underline-offset-2 hover:underline" to={`/backtests/${last.run_id}`}>
                      {line.last}
                    </Link>
                  ) : (
                    line.last
                  )}
                </p>
              )}
              {line.reference && (
                <p className={line.tone === 'warning' ? 'text-warning' : 'text-muted-foreground'}>
                  {line.outside > 0 ? `${line.outside} day${line.outside === 1 ? '' : 's'} outside it. ` : ''}
                  {line.reference}
                </p>
              )}
              {entry && (entry.days.length > 0 || entry.report) && (
                <Link className="text-muted-foreground underline underline-offset-2 hover:text-foreground" to={paperTradingHref(row.id)}>
                  Every day on paper
                </Link>
              )}
            </div>
          )
        },
      },
      {
        id: 'actions',
        header: 'Actions',
        align: 'right',
        cell: ({ row }) => (
          <div className="flex justify-end gap-1">
            <AskAgentLink subject={{ kind: 'rule', id: row.id }} label={`Ask the AI agent about ${row.name}`} compact />
            <Hint content="Edit">
              <Button variant="ghost" size="icon" aria-label={`Edit ${row.name}`} onClick={() => setDialog({ rule: row, start: 'edit' })}>
                <Pencil className="size-4" />
              </Button>
            </Hint>
            {/* A live rule's Delete is disabled: its hint says why, reached through a wrapper that takes the focus. */}
            <Hint content={row.mode === 'live' ? 'Switch it off before deleting it' : 'Delete'} wrap={row.mode === 'live'}>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Delete ${row.name}`}
                disabled={row.mode === 'live'}
                onClick={() => askDelete(row)}
              >
                <Trash2 className="size-4" />
              </Button>
            </Hint>
          </div>
        ),
      },
    ],
    // askMode reads the latest rules through the mutation; the columns change with what they show.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [states, timezone, actionsEnabled, rules?.revision, paper, verdicts],
  )

  const runnable = scripts.filter((script) => script.kind !== 'library')
  const change = changing
  const readiness =
    change?.mode === 'live' ? liveReadiness(change.rule, states[change.rule.id], paper?.rules[change.rule.id], Date.now(), timezone) : []

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Signal rules"
        description="WATA's own signals: a script run on each bar of an asset as it closes, its alerts turned into signals for the trader."
        actions={
          <Button
            size="sm"
            leftIcon={<Plus className="size-4" />}
            disabled={!rules || runnable.length === 0}
            onClick={() => setDialog({ rule: null, start: 'edit' })}
          >
            New rule
          </Button>
        }
      />

      {engineQuery.data && <EnginePanel engine={engineQuery.data} timeZone={timezone} />}

      <QueryState isPending={rulesQuery.isPending} error={rulesQuery.error} hasData={!!rules}>
        {rules && (
          <AdvancedDataTable<TaRule>
            title="Rules"
            searchPlaceholder="Search…"
            emptyTitle="No signal rule yet"
            emptyDescription="Make one with “New rule”: it starts in shadow, its signals journaled and never sent."
            data={rules.rules}
            columns={columns}
            getRowId={(row) => row.id}
            defaultPageSize={20}
            rowLabel={ruleName}
            isRowSelectable={selectableRule}
            selectedRowIds={chosenIds}
            onSelectionChange={setSelected}
            selectionBasket={{
              noun: ['rule', 'rules'],
              actions: [
                {
                  id: 'off',
                  label: 'Switch off',
                  icon: <PowerOff className="size-3.5" />,
                  disabled: allIn('off') || bulkMutation.isPending,
                  title: allIn('off') ? 'They are off already' : undefined,
                  onClick: () => bulkMutation.mutate({ action: 'off', targets: chosen }),
                },
                {
                  id: 'shadow',
                  label: 'Switch to shadow',
                  icon: <Eye className="size-3.5" />,
                  disabled: allIn('shadow') || bulkMutation.isPending,
                  title: allIn('shadow') ? 'They are in shadow already' : undefined,
                  onClick: () => bulkMutation.mutate({ action: 'shadow', targets: chosen }),
                },
                {
                  id: 'delete',
                  label: `Delete ${chosen.length}`,
                  icon: <Trash2 className="size-3.5" />,
                  variant: 'danger',
                  disabled: bulkMutation.isPending,
                  onClick: () => {
                    bulkMutation.reset()
                    setDeletingMany(chosen)
                  },
                },
              ],
            }}
          />
        )}
      </QueryState>
      {bulkError && !deletingMany && (
        <Alert tone="danger" role="alert">
          <AlertDescription>{bulkError}</AlertDescription>
        </Alert>
      )}
      {modeMutation.error && !changing && (
        <Alert tone="danger" role="alert">
          <AlertDescription>{conflictText(modeMutation.error)}</AlertDescription>
        </Alert>
      )}

      {rules && <SignalJournal rules={rules.rules} timeZone={timezone} />}
      {rules && rules.rules.length > 0 && <ComparisonPanel rules={rules.rules} timeZone={timezone} />}

      {rules && shownDialog && (
        <RuleDialog
          open
          rule={shownDialog.rule}
          draft={'draft' in shownDialog ? shownDialog.draft : undefined}
          start={shownDialog.start}
          rules={rules}
          scripts={scripts}
          onClose={closeDialog}
        />
      )}

      <RuleModeDialog change={change} revision={rules?.revision} readiness={readiness} onClose={() => setChanging(null)}
        verdict={change ? <AgentVerdict about={`rule:${change.rule.id}`} changedAt={change.rule.run_changed_at} /> : null} />
      <AgentSheet subject={about && aboutRule ? { ...about, label: aboutRule.name } : undefined} />

      <SettleModal
        open={deletingMany !== null}
        onClose={() => setDeletingMany(null)}
        title={`Delete ${rulesCounted(deletingMany?.length ?? 0)}?`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setDeletingMany(null)} disabled={bulkMutation.isPending}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              size="sm"
              isLoading={bulkMutation.isPending}
              onClick={() => deletingMany && bulkMutation.mutate({ action: 'delete', targets: deletingMany })}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-secondary">
          The engine stops running them. Their signals stay in the journal; their scripts stay on the Indicators page.
        </p>
        <ul className="mt-3 flex max-h-48 flex-col gap-1 overflow-auto text-sm">
          {(deletingMany ?? []).map((rule) => (
            <li key={rule.id} className="truncate">
              {rule.name}
            </li>
          ))}
        </ul>
        {bulkError && (
          <Alert tone="danger" role="alert" className="mt-3">
            <AlertDescription>{bulkError}</AlertDescription>
          </Alert>
        )}
      </SettleModal>

      <DeleteRuleDialog
        rule={deleting}
        pending={deleteMutation.isPending}
        error={deleteMutation.error}
        onCancel={() => setDeleting(null)}
        onDelete={(rule) => deleteMutation.mutate(rule)}
      />
    </div>
  )
}
