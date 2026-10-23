import { useQueries, useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router'

import { compareColor, MAX_COMPARED } from '@/components/charts/compare'
import { CompareCurvesChart, CurvesLegend } from '@/components/charts/PerformanceCharts'
import { PageHeader, Panel } from '@/components/Panel'
import { RouterLink } from '@/components/RouterLink'
import { PnlValue, QueryState } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { SmartComparison } from '@/components/ui/smart-comparison'
import type { ComparisonCategory, ComparisonFeature, ComparisonPlan } from '@/components/ui/smart-comparison'
import { useSession } from '@/features/auth/auth-context'
import { barsLabel } from '@/features/ta/signals/ruleModel'
import { api } from '@/lib/api'
import { formatDay, formatMoney, formatNumber, formatPercent } from '@/lib/format'
import type { BacktestRun } from '@/lib/types'

import { assetLabel, compareCurves, comparisonWarnings, differingInputs, differingSettings, sourceLabel } from './model'
import type { DifferingRow } from './model'
import { BacktestsDisabled } from './parts'

interface Measure {
  label: string
  value: (run: BacktestRun) => number | null | undefined
  show: (value: number | null | undefined) => ReactNode
}

const count = (value: number | null | undefined) => formatNumber(value)
const result = (value: number | null | undefined) => <PnlValue value={value} kind="percent" />

const MEASURES: Measure[] = [
  { label: 'Days tested', value: (run) => run.summary?.figures?.days, show: count },
  { label: 'Days with an entry', value: (run) => run.summary?.figures?.days_with_an_entry, show: count },
  { label: 'Trades', value: (run) => run.summary?.figures?.trades, show: count },
  { label: 'Winning trades', value: (run) => run.summary?.figures?.win_rate, show: (value) => formatPercent(value, false, 1) },
  { label: 'Average win', value: (run) => run.summary?.figures?.average_win, show: result },
  { label: 'Average loss', value: (run) => run.summary?.figures?.average_loss, show: result },
  { label: 'Per trade, on average', value: (run) => run.summary?.figures?.expectancy, show: result },
  { label: 'Won for 1 lost', value: (run) => run.summary?.figures?.profit_factor, show: (value) => formatNumber(value, 2) },
  { label: 'Longest losing streak', value: (run) => run.summary?.figures?.longest_losing_streak, show: count },
  { label: 'All days, one on the other', value: (run) => run.summary?.figures?.total_percent, show: result },
  { label: 'Best day', value: (run) => run.summary?.figures?.best_day, show: result },
  { label: 'Worst day', value: (run) => run.summary?.figures?.worst_day, show: result },
  { label: 'Median day', value: (run) => run.summary?.figures?.median_day, show: result },
  { label: 'Worst drawdown', value: (run) => run.summary?.figures?.worst_drawdown, show: result },
  { label: 'In money', value: (run) => run.summary?.money?.profit_loss, show: (value) => <PnlValue value={value} kind="money" /> },
  { label: 'Return on the account', value: (run) => run.summary?.money?.return_percent, show: result },
  { label: 'Average stake', value: (run) => run.summary?.money?.average_stake, show: (value) => formatMoney(value, 'EUR') },
  { label: 'Random runs beaten', value: (run) => run.summary?.against_random?.beaten, show: (value) => formatPercent(value, false, 0) },
]

/** A row of the matrix whose values are text: compared as they read. */
function textRow(id: string, name: string, runs: readonly BacktestRun[], texts: readonly string[]): ComparisonFeature {
  return { id, name, values: Object.fromEntries(runs.map((run, index) => [run.id, texts[index]])) }
}

/** The matrix (EasyUI's comparison): what each run ran, what they did not share, their figures; "only what differs". */
function comparisonOf(runs: readonly BacktestRun[], differing: readonly DifferingRow[]): { plans: ComparisonPlan[]; categories: ComparisonCategory[] } {
  const plans = runs.map((run, index) => ({
    id: run.id,
    name: run.name,
    tagline: sourceLabel(run),
    price: formatPercent(run.summary?.figures?.total_percent, true),
    href: `/backtests/${run.id}`,
    accent: compareColor(index),
  }))
  const ran = [
    textRow('signals', 'Signals', runs, runs.map((run) =>
      run.request.source.kind === 'pine' ? `${run.request.source.rule.name} v${run.request.source.rule.script.version}` : 'Those WATA received',
    )),
    textRow('asset', 'Asset', runs, runs.map((run) => `${assetLabel(run.chart.asset)} · ${barsLabel(run.chart.bars)}`)),
    textRow('period', 'Period', runs, runs.map((run) => `${formatDay(run.from, false)} to ${formatDay(run.to, false)}`)),
  ]
  const figures = MEASURES.map((measure, index) => ({
    id: `figure-${index}`,
    name: measure.label,
    values: Object.fromEntries(runs.map((run) => [run.id, measure.show(measure.value(run))])),
    // Compared by the figure itself, not by how it is drawn.
    keys: Object.fromEntries(runs.map((run) => [run.id, String(measure.value(run) ?? '')])),
  }))
  return {
    plans,
    categories: [
      { id: 'ran', title: 'What it ran', features: ran },
      ...(differing.length > 0
        ? [{ id: 'differs', title: 'What differs', features: differing.map((row) => textRow(`setting-${row.name}`, row.label, runs, row.values)) }]
        : []),
      { id: 'figures', title: 'Figures', features: figures },
    ],
  }
}

function Comparison({ runs }: { runs: BacktestRun[] }) {
  const { backtests_enabled: enabled } = useSession()
  const optionsQuery = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, enabled, staleTime: 60_000 })
  const differing = [...differingInputs(runs), ...differingSettings(runs, optionsQuery.data?.fields ?? [])]
  const curves = compareCurves(runs)
  const { plans, categories } = comparisonOf(runs, differing)

  return (
    <>
      <Panel title="Side by side" bodyClassName="pt-2">
        <SmartComparison
          plans={plans}
          categories={categories}
          label="The runs side by side"
          matrixTitle="Runs compared"
          countLabel={(rows) => `${rows} rows, the total of all days under each run`}
          searchPlaceholder="Find a row…"
          diffLabel="Only what differs"
          emptyText="No row of that name, or none that differs."
          linkComponent={RouterLink}
        />
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
