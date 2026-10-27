// Technical Analysis › Engine monitor: how fast the TA engine runs each rule, how late its signals
// are and what became of them, over the days it keeps what it measures of itself
// (trade.config.ta_engine.monitoring_days). The figures and the checks are the server's
// (src/admin/ta_monitor.py); monitorModel.ts says them, monitorPanels.tsx draws a period of them.

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, AnimatedTabs, CheckList, cn, KpiTile, Meter, PageHeader, Panel, QueryState, Select, StatusBadge } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { Link, useSearchParams } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { TaEngine, TaMonitorNow } from '@/lib/types'

import {
  addressParams,
  checkHref,
  monitorHref,
  periodLabel,
  readAddress,
  recordingLine,
  resultRows,
  ruleName,
  tilesOf,
} from './monitorModel'
import { ChartsPanel, DeliveryPanel, ResultsPanel, RulesPanel } from './monitorPanels'

function EngineLine({ engine }: { engine: TaEngine }) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <StatusBadge status={engine.status} />
      <span>{engine.detail}</span>
      {!engine.enabled && (
        <span className="text-xs text-muted-foreground">
          It runs once <code>trade.config.ta_engine.enabled</code> is true (
          <Link className="underline" to="/config">
            Configuration
          </Link>
          ).
        </span>
      )}
    </div>
  )
}

function NowPanel({ now }: { now: TaMonitorNow }) {
  return (
    <Panel
      id="now"
      title={`The last ${now.window_minutes} minutes`}
      description="What can cost a live signal. A check fails at half of the limit it watches, and the top bar and the System page say the first that failed; the rest is said here alone."
    >
      <div className="grid gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <CheckList checks={now.checks} label="Checks of the last minutes" hrefOf={checkHref} />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-3">
          {tilesOf(now).map((tile) => (
            <KpiTile key={tile.key} label={tile.label} value={tile.value} hint={tile.hint}>
              {tile.ratio !== null && (
                <Meter ratio={tile.ratio} tone={tile.tone} label={`${tile.label}, against its limit`} scale={tile.scale ?? undefined} />
              )}
            </KpiTile>
          ))}
        </div>
      </div>
      {now.notes.length > 0 && (
        <ul aria-label="Notes" className="mt-5 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
          {now.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

export function MonitorPage() {
  const { timezone, backtests_enabled: backtestsEnabled } = useSession()
  const [params, setParams] = useSearchParams()
  const address = readAddress(params)
  const nowQuery = useQuery({ queryKey: ['ta-monitor-now'], queryFn: api.taMonitorNow, refetchInterval: 10_000 })
  const periodQuery = useQuery({
    queryKey: ['ta-monitor', address.hours, address.rule],
    queryFn: () => api.taMonitor(address),
    // A day in bars of ten minutes moves each minute; longer periods, in bars of half an hour or more, hardly.
    refetchInterval: address.hours <= 24 ? 60_000 : 300_000,
    placeholderData: keepPreviousData,
  })
  const engineQuery = useQuery({ queryKey: ['ta-engine'], queryFn: api.taEngine, refetchInterval: 10_000 })
  // Each shadow rule's finished days traded on paper, by the backtests.
  const paperQuery = useQuery({ queryKey: ['backtest-paper'], queryFn: api.backtestPaper, enabled: backtestsEnabled, staleTime: 60_000 })
  const now = nowQuery.data
  const period = periodQuery.data
  const hours = period?.hours ?? address.hours
  const recording = (period ?? now)?.recording
  const line = recording ? recordingLine(recording, timezone) : null
  const go = (next: { hours?: number; rule?: string | null }) =>
    setParams(addressParams({ hours: next.hours ?? hours, rule: next.rule === undefined ? address.rule : next.rule }), { replace: true })
  const rules = period?.rules ?? []
  const options = [
    { value: '', label: 'Every rule' },
    ...rules.map((rule) => ({ value: rule.rule_id, label: ruleName(rule) })),
    ...(address.rule && !rules.some((rule) => rule.rule_id === address.rule)
      ? [{ value: address.rule, label: ruleName({ name: null, rule_id: address.rule }) }]
      : []),
  ]

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Engine monitor"
        description="How fast the TA engine runs each rule, how late its signals are, and what became of them."
        actions={
          <div className="w-64">
            <Select aria-label="Rule" value={address.rule ?? ''} onChange={(event) => go({ rule: event.target.value || null })} options={options} />
          </div>
        }
      />

      {engineQuery.data && <EngineLine engine={engineQuery.data} />}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <AnimatedTabs
          tabs={(period?.periods ?? [hours]).map((offered) => ({
            id: String(offered),
            label: periodLabel(offered),
            href: monitorHref({ hours: offered, rule: address.rule }),
          }))}
          activeTab={String(hours)}
          onChange={(id) => go({ hours: Number(id) })}
          linkComponent={RouterLink}
          renderContent={false}
          tabsContainerClassName="max-w-full overflow-x-auto whitespace-nowrap"
        />
        {line && <p className="text-xs text-muted-foreground">{line.text}</p>}
      </div>
      {line?.warning && (
        <Alert tone="warning">
          <AlertDescription>{line.warning}</AlertDescription>
        </Alert>
      )}
      {recording && !recording.since && (
        <Alert tone="info">
          <AlertDescription>
            Nothing measured yet. The engine measures each tick and each run of a rule from its first start with this version, and keeps
            them {recording.configured_days} day{recording.configured_days === 1 ? '' : 's'} (
            <code>trade.config.ta_engine.monitoring_days</code>). The signals' delivery and results below come from the journals.
          </AlertDescription>
        </Alert>
      )}

      <QueryState isPending={nowQuery.isPending} error={nowQuery.error} hasData={!!now}>
        {now && <NowPanel now={now} />}
      </QueryState>

      <QueryState isPending={periodQuery.isPending} error={periodQuery.error} hasData={!!period}>
        {period && (
          <div className={cn('flex flex-col gap-6 transition-opacity', periodQuery.isPlaceholderData && 'opacity-60')}>
            <RulesPanel period={period} />
            <ChartsPanel period={period} timeZone={timezone} />
            <DeliveryPanel period={period} />
            <ResultsPanel
              rows={resultRows(period.rules, period.results, paperQuery.data, period.start, timezone)}
              backtestsEnabled={backtestsEnabled}
            />
          </div>
        )}
      </QueryState>
    </div>
  )
}
