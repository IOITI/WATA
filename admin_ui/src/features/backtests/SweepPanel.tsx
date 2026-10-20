import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { CompareCurvesChart, CurvesLegend } from '@/components/charts/PerformanceCharts'
import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { formatDay, formatNumber, formatPercent } from '@/lib/format'
import type { BacktestField, BacktestSummary, BacktestSweep } from '@/lib/types'
import { cn } from '@/lib/utils'

import { choiceTrust, daysRange, STRESS_AXES, stressVerdict, walkForwardCurves, walkForwardSentence } from './robustness'
import { axisName, heat, METRICS, sweepGrid, sweepVerdict } from './sweep'
import type { Metric, Part } from './sweep'

const PARTS: { id: Part; label: string }[] = [
  { id: 'tuning', label: 'Days to choose on' },
  { id: 'held_out', label: 'Days held out' },
  { id: 'whole', label: 'All days' },
]

/** A sweep's cells as a table tinted by their value; a cell is a button when it can be picked. */
export function SweepTable({
  sweep,
  metric,
  part,
  labels,
  marked,
  onPick,
  disabled,
}: {
  sweep: BacktestSweep
  metric: Metric
  part: Part
  labels: Record<string, string>
  /** The cell outlined: the one chosen, or the run as it was. */
  marked?: number | null
  onPick?: (cell: number) => void
  disabled?: boolean
}) {
  const grid = sweepGrid(sweep, metric, part)
  const all = grid.cells.flat().map((cell) => cell.value)
  const [first, second] = sweep.axes
  return (
    <div className="overflow-x-auto">
      <table className="text-sm">
        <thead>
          <tr>
            <th scope="col" className="p-2 text-left text-xs font-medium text-muted-foreground">
              {axisName(first, labels)}
              {second ? ` ↓ · ${axisName(second, labels)} →` : ''}
            </th>
            {grid.columns.map((column) => (
              <th key={String(column)} scope="col" className="p-2 text-right text-xs font-medium tabular-nums">
                {String(column)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.rows.map((row, rowIndex) => (
            <tr key={String(row)}>
              <th scope="row" className="p-2 text-left text-xs font-medium tabular-nums">
                {String(row)}
              </th>
              {grid.cells[rowIndex].map((cell) => {
                const tint = heat(cell.value, all, metric.signed)
                const shown = cell.value === null ? '-' : metric.percent ? formatPercent(cell.value, metric.signed) : formatNumber(cell.value)
                const style = {
                  backgroundColor:
                    tint.tone === 'positive'
                      ? `color-mix(in oklab, var(--chart-positive) ${Math.round(tint.strength * 40)}%, transparent)`
                      : tint.tone === 'negative'
                        ? `color-mix(in oklab, var(--chart-negative) ${Math.round(tint.strength * 40)}%, transparent)`
                        : `color-mix(in oklab, var(--foreground) ${Math.round(tint.strength * 12)}%, transparent)`,
                }
                const look = cn('block w-24 rounded-lg px-2 py-1.5 text-right tabular-nums ring-1 ring-border/50', marked === cell.index && 'ring-2 ring-foreground/60')
                return (
                  <td key={cell.index} className="p-1">
                    {onPick ? (
                      <button
                        type="button"
                        title="Make this combination a run of its own"
                        disabled={disabled}
                        onClick={() => onPick(cell.index)}
                        className={cn(look, 'hover:ring-foreground/40')}
                        style={style}
                      >
                        {shown}
                      </button>
                    ) : (
                      <span className={look} style={style}>
                        {shown}
                      </span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** A sweep's combinations side by side, what it says about choosing a value, and a combination made a run of its own. */
export function SweepPanel({ runId, fields, stress = false }: { runId: string; fields: readonly BacktestField[]; stress?: boolean }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [metricId, setMetricId] = useState(METRICS[0].id)
  // A stress sweep chooses nothing: all its days are what it says.
  const [part, setPart] = useState<Part>(stress ? 'whole' : 'tuning')
  const sweepQuery = useQuery({ queryKey: ['backtest-sweep', runId], queryFn: () => api.backtestSweep(runId), staleTime: Infinity })
  const sweep = sweepQuery.data
  const labels = { ...Object.fromEntries(fields.map((field) => [field.name, field.label])), ...(stress ? STRESS_AXES : {}) }
  const cellRun = useMutation({
    mutationFn: (cell: number) => api.runSweepCell(runId, cell),
    onSuccess: async (run) => {
      await queryClient.invalidateQueries({ queryKey: ['backtests'] })
      navigate(`/backtests/${run.id}`)
    },
  })
  const metric = METRICS.find((entry) => entry.id === metricId) ?? METRICS[0]

  return (
    <Panel
      title={stress ? 'Later and dearer fills' : 'The combinations'}
      description={
        stress
          ? 'The same signals on the same days, each order filled later (rows) and at a worse price a turbo (columns) than the run was. Its own fills are the first cell.'
          : 'Each combination traded on the same days and the same signals, with its own account. The values are chosen on the first days; the last ones, held out, say whether the choice holds.'
      }
    >
      <QueryState isPending={sweepQuery.isPending} error={sweepQuery.error} hasData={!!sweep}>
        {sweep &&
          (() => {
            const verdict = stress ? null : sweepVerdict(sweep, labels)
            const stressed = stress ? stressVerdict(sweep) : null
            return (
              <div className="flex flex-col gap-4">
                {verdict && (
                  <Alert tone={verdict.held === false ? 'warning' : 'neutral'}>
                    <AlertDescription>{verdict.sentence}</AlertDescription>
                  </Alert>
                )}
                {stressed && (
                  <Alert tone={stressed.holds === false ? 'warning' : 'neutral'}>
                    <AlertDescription>{stressed.sentence}</AlertDescription>
                  </Alert>
                )}
                <div className="flex flex-wrap gap-2" role="group" aria-label="Figure shown">
                  {METRICS.map((entry) => (
                    <Button key={entry.id} size="sm" variant={entry.id === metric.id ? 'primary' : 'outline'} aria-pressed={entry.id === metric.id} onClick={() => setMetricId(entry.id)}>
                      {entry.label}
                    </Button>
                  ))}
                </div>
                {!stress && (
                  <div className="flex flex-wrap gap-2" role="group" aria-label="Days shown">
                    {PARTS.map((entry) => (
                      <Button key={entry.id} size="sm" variant={entry.id === part ? 'primary' : 'outline'} aria-pressed={entry.id === part} onClick={() => setPart(entry.id)}>
                        {entry.label}
                      </Button>
                    ))}
                  </div>
                )}
                <SweepTable
                  sweep={sweep}
                  metric={metric}
                  part={part}
                  labels={labels}
                  marked={stress ? 0 : verdict?.best}
                  onPick={(cell) => cellRun.mutate(cell)}
                  disabled={cellRun.isPending}
                />
                <p className="text-xs text-muted-foreground">
                  {stress
                    ? 'All the days of the run, each trade as the whole stake.'
                    : sweep.held_out_from
                      ? `Days from ${formatDay(sweep.held_out_from)} are held out (the last ${Math.round(sweep.held_out * 100)} %).`
                      : 'Too few days to hold any out.'}{' '}
                  A click on a combination makes it a run of its own, set against random entries.
                </p>
                {cellRun.error && (
                  <p role="alert" className="text-sm text-negative">
                    {cellRun.error.message}
                  </p>
                )}
              </div>
            )
          })()}
      </QueryState>
    </Panel>
  )
}

/**
 * Whether choosing among a sweep's combinations would have worked: chosen
 * again window after window on the days before (walk-forward), and what the
 * number of combinations tried does to the best one's figures.
 */
export function ChoicePanel({ runId, summary, fields }: { runId: string; summary: BacktestSummary; fields: readonly BacktestField[] }) {
  const sweepQuery = useQuery({ queryKey: ['backtest-sweep', runId], queryFn: () => api.backtestSweep(runId), staleTime: Infinity })
  const sweep = sweepQuery.data
  const labels = Object.fromEntries(fields.map((field) => [field.name, field.label]))
  const trust = choiceTrust(summary)
  const walk = summary.walk_forward
  if (trust.length === 0 && walk === undefined) return null
  return (
    <Panel
      title="Would choosing have worked"
      description="The best of many combinations looks good by chance. These say whether choosing one would have held on days that had no say in it."
    >
      <div className="flex flex-col gap-4">
        {trust.map((entry) => (
          <Alert key={entry.sentence} tone={entry.doubtful ? 'warning' : 'neutral'}>
            <AlertDescription>{entry.sentence}</AlertDescription>
          </Alert>
        ))}
        {trust.length === 0 && <p className="text-sm text-muted-foreground">Too few days to measure how much choosing the best was fitting.</p>}
        {walk === null && <p className="text-sm text-muted-foreground">Too few days for one window of the walk-forward asked for.</p>}
        {walk && sweep && (
          <section aria-label="Walk-forward" className="flex min-w-0 flex-col gap-3">
            <h3 className="text-xs font-medium text-muted-foreground">Walk-forward</h3>
            <p className="text-sm leading-relaxed">{walkForwardSentence(walk, sweep.axes, labels)}</p>
            {(() => {
              const curves = walkForwardCurves(sweep, walk)
              return (
                <>
                  <CurvesLegend curves={curves} />
                  <CompareCurvesChart curves={curves} />
                </>
              )
            })()}
            <div className="overflow-x-auto">
              <table className="w-full whitespace-nowrap text-sm">
                <thead>
                  <tr className="text-xs text-muted-foreground">
                    <th scope="col" className="p-2 text-left font-medium">
                      Chosen on
                    </th>
                    <th scope="col" className="p-2 text-left font-medium">
                      Kept on
                    </th>
                    <th scope="col" className="p-2 text-left font-medium">
                      Chosen
                    </th>
                    <th scope="col" className="p-2 text-right font-medium">
                      Then
                    </th>
                    <th scope="col" className="p-2 text-right font-medium">
                      After
                    </th>
                    <th scope="col" className="p-2 text-right font-medium">
                      Its place after
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {walk.windows.map((window) => (
                    <tr key={window.test[0]} className="border-t border-border/50">
                      <td className="p-2 whitespace-nowrap">{daysRange(window.train)}</td>
                      <td className="p-2 whitespace-nowrap">{daysRange(window.test)}</td>
                      <td className="p-2 tabular-nums">
                        {(window.values ?? []).map((value, index) => `${axisName(sweep.axes[index], labels).toLowerCase()} ${String(value)}`).join(', ')}
                      </td>
                      <td className="p-2 text-right tabular-nums">{formatPercent(window.train_percent, true)}</td>
                      <td className="p-2 text-right tabular-nums">{formatPercent(window.test_percent, true)}</td>
                      <td className="p-2 text-right tabular-nums">
                        {window.test_rank} of {window.cells}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </Panel>
  )
}
