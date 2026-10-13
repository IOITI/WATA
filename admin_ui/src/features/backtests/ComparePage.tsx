import { useQueries } from '@tanstack/react-query'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'

import { compareColor, MAX_COMPARED } from '@/components/charts/compare'
import { CompareCurvesChart, CurvesLegend } from '@/components/charts/PerformanceCharts'
import type { NamedCurve } from '@/components/charts/PerformanceCharts'
import { PageHeader, Panel } from '@/components/Panel'
import { PnlValue, QueryState } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDay, formatNumber, formatPercent } from '@/lib/format'
import type { BacktestFigures, BacktestRun } from '@/lib/types'

import { commonLeverages, compounded, differing, figuresAt, middleLeverage, runName, showValue, wording } from './model'
import { BacktestsDisabled, BackToRuns, LeveragePicker } from './parts'

interface Measure {
  label: string
  cell: (figures: BacktestFigures) => ReactNode
}

const MEASURES: Measure[] = [
  { label: 'Days tested', cell: (figures) => formatNumber(figures.days) },
  { label: 'Days with an entry', cell: (figures) => formatNumber(figures.days_with_an_entry) },
  { label: 'Trades', cell: (figures) => formatNumber(figures.trades) },
  { label: 'Winning trades', cell: (figures) => formatPercent(figures.win_rate, false, 1) },
  { label: 'Average win', cell: (figures) => <PnlValue value={figures.average_win} kind="percent" /> },
  { label: 'Average loss', cell: (figures) => <PnlValue value={figures.average_loss} kind="percent" /> },
  { label: 'Per trade, on average', cell: (figures) => <PnlValue value={figures.expectancy} kind="percent" /> },
  { label: 'Won for 1 lost', cell: (figures) => formatNumber(figures.profit_factor, 2) },
  { label: 'All days, one on the other', cell: (figures) => <PnlValue value={figures.total_percent} kind="percent" /> },
  { label: 'Best day', cell: (figures) => <PnlValue value={figures.best_day} kind="percent" /> },
  { label: 'Worst day', cell: (figures) => <PnlValue value={figures.worst_day} kind="percent" /> },
  { label: 'Worst drawdown', cell: (figures) => <PnlValue value={figures.worst_drawdown} kind="percent" /> },
  { label: 'Index points', cell: (figures) => formatNumber(figures.index_points, 1) },
  { label: 'Random runs beaten', cell: (figures) => formatPercent(figures.against_random?.beaten, false, 0) },
  {
    label: 'In money, as WATA sizes',
    cell: (figures) => (figures.money ? <PnlValue value={figures.money.profit_loss} kind="money" /> : '-'),
  },
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
  const shared = commonLeverages(runs)
  const [chosen, setChosen] = useState<number | null>(null)
  const leverage = chosen !== null && shared.includes(chosen) ? chosen : shared.length > 0 ? middleLeverage(shared) : null

  const dayQueries = useQueries({
    queries: runs.map((run) => ({
      queryKey: ['backtest-days', run.id, leverage],
      queryFn: () => api.backtestDays(run.id, leverage ?? 0),
      enabled: leverage !== null,
      staleTime: Infinity,
    })),
  })

  if (leverage === null) {
    return (
      <Alert tone="warning">
        <AlertDescription>
          These runs were not made with any leverage in common: their figures cannot be set side by side.
        </AlertDescription>
      </Alert>
    )
  }

  const { parameters, rules } = differing(runs)
  const curves: NamedCurve[] = runs.map((run, index) => {
    const soFar: number[] = []
    return {
      id: run.id,
      name: runName(run),
      points: (dayQueries[index].data?.items ?? []).map((day) => {
        soFar.push(day.day_percent)
        return { day: day.day, value: compounded(soFar) }
      }),
    }
  })

  return (
    <>
      <LeveragePicker leverages={shared} value={leverage} onChange={setChosen} />

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
                      {runName(run)}
                    </Link>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              <Row label="Period">
                {runs.map((run) => (
                  <Cell key={run.id}>
                    {formatDay(run.from, false)} to {formatDay(run.to, false)}
                  </Cell>
                ))}
              </Row>
              {parameters.map((name) => (
                <Row key={`parameter-${name}`} label={wording(name)}>
                  {runs.map((run) => (
                    <Cell key={run.id}>
                      {run.parameters[name] === undefined ? (
                        <span className="text-muted-foreground">default</span>
                      ) : (
                        showValue(run.parameters[name])
                      )}
                    </Cell>
                  ))}
                </Row>
              ))}
              {rules.map((name) => (
                <Row key={`rule-${name}`} label={`Rule: ${wording(name).toLowerCase()}`}>
                  {runs.map((run) => (
                    <Cell key={run.id}>
                      {run.rules[name] === undefined ? (
                        <span className="text-muted-foreground">live</span>
                      ) : (
                        showValue(run.rules[name])
                      )}
                    </Cell>
                  ))}
                </Row>
              ))}
              {MEASURES.map((measure) => (
                <Row key={measure.label} label={measure.label}>
                  {runs.map((run) => {
                    const figures = figuresAt(run, leverage)
                    return <Cell key={run.id}>{figures ? measure.cell(figures) : '-'}</Cell>
                  })}
                </Row>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel
        title="All days so far, one applied on the other"
        description="In percent, as WATA counts a day. Each run over the days it tested."
      >
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
  const runs = runQueries.map((query) => query.data).filter((run): run is BacktestRun => !!run && !!run.summary)
  const pending = runQueries.some((query) => query.isPending)
  const error = runQueries.find((query) => query.error)?.error ?? null
  const unfinished = runQueries.filter((query) => query.data && !query.data.summary).length

  return (
    <>
      <BackToRuns />
      <PageHeader title="Compare backtests" description="The same measures for each run, and what each one changed." />

      {!enabled ? (
        <BacktestsDisabled />
      ) : ids.length < 2 ? (
        <Alert tone="info">
          <AlertDescription>Tick two runs or more in the list of backtests, then “Compare”.</AlertDescription>
        </Alert>
      ) : (
        <QueryState isPending={pending} error={error} hasData={runs.length > 0}>
          {asked.length > MAX_COMPARED && (
            <Alert tone="warning">
              <AlertDescription>
                {MAX_COMPARED} runs at most are compared at once: the first {MAX_COMPARED} of the {asked.length} chosen
                are shown.
              </AlertDescription>
            </Alert>
          )}
          {unfinished > 0 && (
            <Alert tone="warning">
              <AlertDescription>
                {unfinished} of the runs chosen {unfinished === 1 ? 'has' : 'have'} no result (not finished, or failed)
                and {unfinished === 1 ? 'is' : 'are'} left out.
              </AlertDescription>
            </Alert>
          )}
          {/* Made again for another set of runs: the leverage chosen belongs to that set. */}
          {runs.length > 0 && <Comparison key={runs.map((run) => run.id).join(',')} runs={runs} />}
        </QueryState>
      )}
    </>
  )
}
