// Technical Analysis › Signal rules: WATA's own signals. A rule runs a script on
// the bars of an asset of the Capital.com feed, and maps its alerts to signals:
// journaled only in shadow, sent to the trader once the rule is live. The TA
// engine (src/ta_engine) runs them; this page makes and switches them, and shows
// what the engine did.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { ButtonLink } from '@/components/ButtonLink'
import { CheckList } from '@/components/CheckList'
import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { PageHeader, Panel } from '@/components/Panel'
import { ClaudeBadge, QueryState, ServiceStatusBadge } from '@/components/status'
import { useToast } from '@/components/toast-context'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/form'
import { SettleModal } from '@/components/ui/settle-modal'
import { useSession } from '@/features/auth/auth-context'
import { madeByClaude } from '@/features/system/claude'
import { monitorHref } from '@/features/ta/monitor/monitorModel'
import { api, ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import type { TaEngine, TaRule, TaRuleMode, TaRules } from '@/lib/types'

import { ComparisonPanel } from './ComparisonPanel'
import { RuleDialog } from './RuleDialog'
import type { RuleDialogStart } from './RuleDialog'
import { barsLabel, MODE_LABEL, MODE_TONE, mappedAlerts, newDraft, ruleDraftOfRun, updatesOf } from './ruleModel'
import type { RuleDraft } from './ruleModel'
import { paperLine } from './paper'
import { liveReadiness } from './readiness'
import { SignalJournal } from './SignalJournal'

const STATE_LABEL: Record<string, { label: string; tone: 'neutral' | 'success' | 'warning' | 'danger' }> = {
  loading: { label: 'Loading', tone: 'warning' },
  waiting: { label: 'Waiting for bars', tone: 'warning' },
  running: { label: 'Running', tone: 'success' },
  error: { label: 'Failing', tone: 'danger' },
  off: { label: 'Off', tone: 'neutral' },
}

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
        <ServiceStatusBadge status={engine.status} />
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

interface ModeChange {
  rule: TaRule
  mode: TaRuleMode
}

export function SignalRulesPage() {
  const { timezone, actions_enabled: actionsEnabled, backtests_enabled: backtestsEnabled } = useSession()
  const toast = useToast()
  const queryClient = useQueryClient()
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, refetchOnWindowFocus: false })
  const scriptsQuery = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts })
  const engineQuery = useQuery({ queryKey: ['ta-engine'], queryFn: api.taEngine, refetchInterval: 10_000 })
  // Each shadow rule's finished days traded on paper, by the backtests.
  const paperQuery = useQuery({ queryKey: ['backtest-paper'], queryFn: api.backtestPaper, enabled: backtestsEnabled, staleTime: 60_000 })
  const paper = paperQuery.data
  const rules: TaRules | undefined = rulesQuery.data
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
  const closeDialog = () => {
    setDialog(null)
    if (askedScript || askedRun) setParams({}, { replace: true })
  }
  const [changing, setChanging] = useState<ModeChange | null>(null)
  const [deleting, setDeleting] = useState<TaRule | null>(null)

  const refresh = () => Promise.all([['ta-rules'], ['ta-engine']].map((queryKey) => queryClient.invalidateQueries({ queryKey })))
  const conflictText = (error: Error | null) =>
    error instanceof ApiError && error.status === 409 && error.message.includes('changed meanwhile')
      ? 'The rules changed since this page read them: they are read again, try once more.'
      : error instanceof ApiError && error.status === 403
        ? 'Operator actions are disabled in the configuration (admin.actions_enabled): no rule goes live.'
        : error?.message ?? null

  const modeMutation = useMutation({
    mutationFn: ({ rule, mode }: ModeChange) => api.setTaRuleMode(rule.id, mode, rules!.revision),
    onSuccess: async (saved) => {
      setChanging(null)
      toast({
        title: `${saved.rule.name}: ${MODE_LABEL[saved.rule.mode].toLowerCase()}`,
        description:
          saved.rule.mode === 'live'
            ? 'Its signals go to the trader from its next bar.'
            : saved.rule.mode === 'shadow'
              ? 'Its signals are journaled, never sent.'
              : 'The engine stops running it.',
        variant: 'success',
      })
      await refresh()
    },
    onError: () => void refresh(),
  })
  const deleteMutation = useMutation({
    mutationFn: (rule: TaRule) => api.deleteTaRule(rule.id, rules!.revision),
    onSuccess: async () => {
      setDeleting(null)
      toast({ title: 'Signal rule deleted', variant: 'success' })
      await refresh()
    },
    onError: () => void refresh(),
  })

  const askMode = (rule: TaRule, mode: TaRuleMode) => {
    if (mode === rule.mode) return
    // Off and shadow change nothing that trades: at once. Live, and leaving it, are said first.
    if (mode !== 'live' && rule.mode !== 'live') modeMutation.mutate({ rule, mode })
    else {
      modeMutation.reset()
      setChanging({ rule, mode })
    }
  }

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
              <span className="font-medium">{row.name}</span>
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
          const shown = STATE_LABEL[state.state] ?? { label: state.state, tone: 'neutral' as const }
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
              <span className="font-medium">{line.total}</span>
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
            <Button variant="ghost" size="icon" aria-label={`Edit ${row.name}`} title="Edit" onClick={() => setDialog({ rule: row, start: 'edit' })}>
              <Pencil className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Delete ${row.name}`}
              title={row.mode === 'live' ? 'Switch it off before deleting it' : 'Delete'}
              disabled={row.mode === 'live'}
              onClick={() => {
                deleteMutation.reset()
                setDeleting(row)
              }}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ),
      },
    ],
    // askMode reads the latest rules through the mutation; the columns change with what they show.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [states, timezone, actionsEnabled, rules?.revision, paper],
  )

  const runnable = scripts.filter((script) => script.kind !== 'library')
  const change = changing
  const goingLive = change?.mode === 'live'

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
          />
        )}
      </QueryState>
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

      <ConfirmLiveAction
        open={change !== null}
        title={goingLive ? `Switch ${change?.rule.name} to live` : `Take ${change?.rule.name} off live`}
        gesture={goingLive ? 'slide' : 'button'}
        confirmLabel={goingLive ? 'Slide to go live' : `Switch to ${change ? MODE_LABEL[change.mode].toLowerCase() : ''}`}
        doneLabel={goingLive ? 'Live' : 'Done'}
        pending={modeMutation.isPending}
        error={conflictText(modeMutation.error)}
        warning={
          goingLive
            ? `The web server keeps one signal per indice, whoever sends it: switch TradingView's alerts for ${change?.rule.indice} off first, or both will trade it.`
            : undefined
        }
        description={
          change &&
          (goingLive ? (
            <div className="space-y-2">
              <p>
                From its next bar, the alerts of <strong>{change.rule.name}</strong> ({change.rule.asset.key},{' '}
                {barsLabel(change.rule.bars)}) become signals the trader acts on, for <strong>{change.rule.indice}</strong>:
              </p>
              <ul className="list-disc pl-5">
                {mappedAlerts(change.rule).map((entry) => (
                  <li key={entry}>{entry}</li>
                ))}
              </ul>
              <p>The trader applies its usual rules to each of them. The switch is recorded in the audit trail.</p>
              <div className="space-y-2 pt-2">
                <p className="font-medium">Is it ready?</p>
                <CheckList
                  checks={liveReadiness(change.rule, states[change.rule.id], paper?.rules[change.rule.id], Date.now(), timezone)}
                  label="Is it ready?"
                />
                <p className="text-xs text-muted-foreground">What this page knows of it. Nothing here stops the switch: it is yours.</p>
              </div>
            </div>
          ) : (
            <p>
              Its signals stop going to the trader from now on{change.mode === 'shadow' ? ': they are journaled only' : ''}.
            </p>
          ))
        }
        onConfirm={() => change && modeMutation.mutate(change)}
        onClose={() => setChanging(null)}
      />

      <SettleModal
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={`Delete ${deleting?.name ?? ''}`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setDeleting(null)} disabled={deleteMutation.isPending}>
              Cancel
            </Button>
            <Button variant="destructive" size="sm" isLoading={deleteMutation.isPending} onClick={() => deleting && deleteMutation.mutate(deleting)}>
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-secondary">
          The engine stops running it. Its signals stay in the journal; the script stays on the Indicators page.
        </p>
        {deleteMutation.error && (
          <Alert tone="danger" role="alert" className="mt-3">
            <AlertDescription>{conflictText(deleteMutation.error)}</AlertDescription>
          </Alert>
        )}
      </SettleModal>
    </div>
  )
}
