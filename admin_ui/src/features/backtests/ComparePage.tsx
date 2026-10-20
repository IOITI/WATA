import { useQueries, useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'

import { compareColor, MAX_COMPARED } from '@/components/charts/compare'
import { CompareCurvesChart, CurvesLegend } from '@/components/charts/PerformanceCharts'
import { PageHeader, Panel } from '@/components/Panel'
import { PnlValue, QueryState } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { useSession } from '@/features/auth/auth-context'
import { barsLabel } from '@/features/ta/signals/ruleModel'
import { api } from '@/lib/api'
import { formatDay, formatMoney, formatNumber, formatPercent } from '@/lib/format'
import type { BacktestRun } from '@/lib/types'

import { assetLabel, compareCurves, comparisonWarnings, differingInputs, differingSettings } from './model'
import { BacktestsDisabled } from './parts'

interface Measure {
  label: string
  cell: (run: BacktestRun) => ReactNode
}

const MEASURES: Measure[] = [
  { label: 'Days tested', cell: (run) => formatNumber(run.summary?.figures?.days) },
  { label: 'Days with an entry', cell: (run) => formatNumber(run.summary?.figures?.days_with_an_entry) },
  { label: 'Trades', cell: (run) => formatNumber(run.summary?.figures?.trades) },
  { label: 'Winning trades', cell: (run) => formatPercent(run.summary?.figures?.win_rate, false, 1) },
  { label: 'Average win', cell: (run) => <PnlValue value={run.summary?.figures?.average_win} kind="percent" /> },
  { label: 'Average loss', cell: (run) => <PnlValue value={run.summary?.figures?.average_loss} kind="percent" /> },
  { label: 'Per trade, on average', cell: (run) => <PnlValue value={run.summary?.figures?.expectancy} kind="percent" /> },
  { label: 'Won for 1 lost', cell: (run) => formatNumber(run.summary?.figures?.profit_factor, 2) },
  { label: 'Longest losing streak', cell: (run) => formatNumber(run.summary?.figures?.longest_losing_streak) },
  { label: 'All days, one on the other', cell: (run) => <PnlValue value={run.summary?.figures?.total_percent} kind="percent" /> },
  { label: 'Best day', cell: (run) => <PnlValue value={run.summary?.figures?.best_day} kind="percent" /> },
  { label: 'Worst day', cell: (run) => <PnlValue value={run.summary?.figures?.worst_day} kind="percent" /> },
  { label: 'Median day', cell: (run) => <PnlValue value={run.summary?.figures?.median_day} kind="percent" /> },
  { label: 'Worst drawdown', cell: (run) => <PnlValue value={run.summary?.figures?.worst_drawdown} kind="percent" /> },
  { label: 'In money', cell: (run) => <PnlValue value={run.summary?.money?.profit_loss} kind="money" /> },
  { label: 'Return on the account', cell: (run) => <PnlValue value={run.summary?.money?.return_percent} kind="percent" /> },
  { label: 'Average stake', cell: (run) => formatMoney(run.summary?.money?.average_stake, 'EUR') },
  { label: 'Random runs beaten', cell: (run) => formatPercent(run.summary?.against_random?.beaten, false, 0) },
]

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <tr>
      <th scope="row" className="py-2.5 pr-4 text-left font-normal text-muted-foreground">
        {label}
      </th>
      {children}
    </tr>
  )
}

function Cell({ children }: { children: ReactNode }) {
  return <td className="py-2.5 pl-4 text-right tabular-nums">{children}</td>
}

function Comparison({ runs }: { runs: BacktestRun[] }) {
  const { backtests_enabled: enabled } = useSession()
  const optionsQuery = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, enabled, staleTime: 60_000 })
  const differing = [...differingInputs(runs), ...differingSettings(runs, optionsQuery.data?.fields ?? [])]
  const curves = compareCurves(runs)

  return (
    <>
      <Panel title="Side by side" bodyClassName="pt-2">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="py-2 pr-4" />
                {runs.map((run, index) => (
                  <th key={run.id} scope="col" className="py-2 pl-4 text-right align-bottom font-medium">
                    <Link to={`/backtests/${run.id}`} className="inline-flex items-center gap-1.5 underline-offset-4 hover:underline">
                      <span aria-hidden className="h-0.5 w-4 shrink-0 rounded-full" style={{ backgroundColor: compareColor(index) }} />
                      {run.name}
                    </Link>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              <Row label="Signals">
                {runs.map((run) => (
                  <Cell key={run.id}>
                    {run.request.source.kind === 'pine'
                      ? `${run.request.source.rule.name} v${run.request.source.rule.script.version}`
                      : 'Those WATA received'}
                  </Cell>
                ))}
              </Row>
              <Row label="Asset">
                {runs.map((run) => (
                  <Cell key={run.id}>
                    {assetLabel(run.chart.asset)} · {barsLabel(run.chart.bars)}
                  </Cell>
                ))}
              </Row>
              <Row label="Period">
                {runs.map((run) => (
                  <Cell key={run.id}>
                    {formatDay(run.from, false)} to {formatDay(run.to, false)}
                  </Cell>
                ))}
              </Row>
              {differing.map((row) => (
                <Row key={row.name} label={row.label}>
                  {row.values.map((value, index) => (
                    <Cell key={runs[index].id}>{value}</Cell>
                  ))}
                </Row>
              ))}
              {MEASURES.map((measure) => (
                <Row key={measure.label} label={measure.label}>
                  {runs.map((run) => (
                    <Cell key={run.id}>{measure.cell(run)}</Cell>
                  ))}
                </Row>
              ))}
            </tbody>
          </table>
        </div>
        {differing.length === 0 && optionsQuery.data && (
          <p className="mt-3 text-xs text-muted-foreground">They were made with the same settings and inputs.</p>
        )}
      </Panel>

      <Panel title="All days so far, one applied on the other" description="In percent, as WATA counts a day. Each run over the days it traded.">
        <div className="flex flex-col gap-3">
          {/* In the body, where it can wrap: beside the title it would push a phone's page sideways. */}
          <CurvesLegend curves={curves} />
          <CompareCurvesChart curves={curves} />
        </div>
      </Panel>
    </>
  )
}

export function ComparePage() {
  const { backtests_enabled: enabled } = useSession()
  const [search] = useSearchParams()
  const asked = [...new Set((search.get('runs') ?? '').split(',').filter(Boolean))]
  const ids = asked.slice(0, MAX_COMPARED)

  const runQueries = useQueries({
    // Nothing is asked for a comparison that will not be shown.
    queries: ids.map((id) => ({ queryKey: ['backtest', id], queryFn: () => api.backtest(id), enabled: enabled && ids.length >= 2 })),
  })
  const loaded = runQueries.map((query) => query.data).filter((run): run is BacktestRun => !!run)
  const runs = loaded.filter((run) => run.status.state === 'done' && run.kind === 'run' && run.summary)
  const pending = runQueries.some((query) => query.isPending)
  const error = runQueries.find((query) => query.error)?.error ?? null
  const leftOut = loaded.length - runs.length
  const warnings = comparisonWarnings(runs)

  return (
    <>
      <PageHeader title="Compare backtests" description="The same figures for each run, what each one changed, and their days one applied on the other." />

      {!enabled ? (
        <BacktestsDisabled />
      ) : ids.length < 2 ? (
        <Alert tone="info">
          <AlertDescription>Tick two runs or more in the list of backtests, then “Compare”.</AlertDescription>
        </Alert>
      ) : (
        <QueryState isPending={pending} error={error} hasData={runs.length > 0}>
          <div className="flex flex-col gap-6">
            {asked.length > MAX_COMPARED && (
              <Alert tone="warning">
                <AlertDescription>
                  {MAX_COMPARED} runs at most are compared at once: the first {MAX_COMPARED} of the {asked.length} chosen are shown.
                </AlertDescription>
              </Alert>
            )}
            {leftOut > 0 && (
              <Alert tone="warning">
                <AlertDescription>
                  {leftOut} of the runs chosen {leftOut === 1 ? 'has' : 'have'} no result to compare (a sweep, not finished, or failed) and{' '}
                  {leftOut === 1 ? 'is' : 'are'} left out.
                </AlertDescription>
              </Alert>
            )}
            {warnings.length > 0 && (
              <Alert tone="neutral">
                <AlertDescription>{warnings.join(' ')}</AlertDescription>
              </Alert>
            )}
            {runs.length > 0 && <Comparison runs={runs} />}
          </div>
        </QueryState>
      )}
    </>
  )
}
