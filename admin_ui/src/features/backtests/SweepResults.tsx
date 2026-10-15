import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { Panel } from '@/components/Panel'
import { PnlValue } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { formatDay, formatNumber, formatPercent } from '@/lib/format'
import type { BacktestFigures, BacktestRun, BacktestSweep } from '@/lib/types'
import { cn } from '@/lib/utils'

import { cellName, heat, METRICS, showValue, sweepGrid, sweepVerdict, wording } from './model'
import type { Grid, Metric, Part } from './model'

const TINT = { positive: 'var(--chart-positive)', negative: 'var(--chart-negative)', neutral: 'var(--chart-series)' }
// The most a cell is tinted: its figure stays the thing that is read.
const DEEPEST_TINT = 42

function shown(metric: Metric, value: number | null): string {
  return metric.percent ? formatPercent(value, metric.signed) : formatNumber(value)
}

interface HeatGridProps {
  sweep: BacktestSweep
  grid: Grid
  metric: Metric
  title: string
  selected: number | null
  onSelect: (cell: number) => void
}

/** The cells of one part of the period: each a figure, tinted by where it stands among the others. */
function HeatGrid({ sweep, grid, metric, title, selected, onSelect }: HeatGridProps) {
  const [first, second] = sweep.axes
  const all = grid.cells.flat().map((cell) => cell.value)
  return (
    <div className="min-w-0">
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-1 text-sm">
          <caption className="mb-2 text-left text-xs font-medium text-muted-foreground">{title}</caption>
          <thead>
            <tr>
              <th scope="col" className="px-2 py-1 text-left text-xs font-normal text-muted-foreground">
                {wording(first.name)}
              </th>
              {grid.columns.map((column) => (
                <th key={String(column)} scope="col" className="px-2 py-1 text-right text-xs font-normal text-muted-foreground">
                  {second ? `${wording(second.name)} ${showValue(column)}` : metric.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((row, rowNumber) => (
              <tr key={String(row)}>
                <th scope="row" className="px-2 py-1 text-left font-normal tabular-nums">
                  {showValue(row)}
                </th>
                {grid.cells[rowNumber].map((cell) => {
                  const tint = heat(cell.value, all, metric.signed)
                  return (
                    <td key={cell.index} className="p-0">
                      <button
                        type="button"
                        aria-pressed={cell.index === selected}
                        aria-label={`${cellName(sweep.axes, sweep.cells[cell.index])}: ${shown(metric, cell.value)}`}
                        onClick={() => onSelect(cell.index)}
                        className={cn(
                          'w-full rounded-md px-2 py-1.5 text-right tabular-nums outline-none transition-shadow',
                          'focus-visible:ring-2 focus-visible:ring-ring',
                          cell.index === selected ? 'ring-2 ring-foreground' : 'hover:ring-1 hover:ring-border',
                        )}
                        style={{
                          backgroundColor: `color-mix(in srgb, ${TINT[tint.tone]} ${Math.round(tint.strength * DEEPEST_TINT)}%, transparent)`,
                        }}
                      >
                        {shown(metric, cell.value)}
                      </button>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const PARTS: { part: Part | 'whole'; label: string }[] = [
  { part: 'tuning', label: 'Chosen on' },
  { part: 'held_out', label: 'Held out' },
  { part: 'whole', label: 'Whole period' },
]

function Figure({ figures, read }: { figures: BacktestFigures | null; read: (figures: BacktestFigures) => React.ReactNode }) {
  return <td className="py-2 pl-4 text-right tabular-nums">{figures ? read(figures) : '-'}</td>
}

/**
 * What a sweep found: every combination on the days a value may be chosen
 * on, beside the same combinations on the days held out, and what the best of
 * the first became on the second.
 */
export function SweepResults({ run }: { run: BacktestRun }) {
  const sweep = run.sweep
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const verdict = sweep ? sweepVerdict(sweep) : null
  const [metricId, setMetricId] = useState(METRICS[0].id)
  const [selected, setSelected] = useState<number | null>(verdict?.best ?? null)
  const metric = METRICS.find((candidate) => candidate.id === metricId) ?? METRICS[0]

  const cellMutation = useMutation({
    mutationFn: (cell: number) => api.runSweepCell(run.id, cell),
    onSuccess: (made) => {
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      navigate(`/backtests/${made.id}`)
    },
  })

  if (!sweep || !verdict) return null
  const without = sweep.days_without_prices ?? []
  const chosen = selected !== null ? sweep.cells[selected] : null
  const heldOutFrom = sweep.held_out.from

  return (
    <>
      {without.length > 0 && (
        <Alert tone="warning">
          <AlertDescription>
            No prices for {without.length} weekday{without.length === 1 ? '' : 's'} of the period (not fetched, or the
            market was closed): {without.map((day) => formatDay(day, false)).join(', ')}.
          </AlertDescription>
        </Alert>
      )}

      <Alert tone={verdict.held === false ? 'warning' : 'info'}>
        <AlertTitle>{verdict.held === false ? 'The best value did not hold up' : 'What the sweep says'}</AlertTitle>
        <AlertDescription>{verdict.sentence}</AlertDescription>
      </Alert>

      <Panel
        title={`${sweep.cells.length} combinations`}
        description={
          heldOutFrom
            ? `Every combination replayed over the whole period. Its figures are given apart for the ${sweep.held_out.tuning_days} days before ${formatDay(heldOutFrom)} and for the ${sweep.held_out.days} from it, which are held out.`
            : 'Every combination replayed over the whole period, which had too few days to hold any out.'
        }
      >
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Figure shown">
            {METRICS.map((candidate) => (
              <Button
                key={candidate.id}
                variant={candidate.id === metric.id ? 'primary' : 'outline'}
                size="sm"
                aria-pressed={candidate.id === metric.id}
                onClick={() => setMetricId(candidate.id)}
              >
                {candidate.label}
              </Button>
            ))}
          </div>
          <div className="grid gap-8 lg:grid-cols-2">
            <HeatGrid
              sweep={sweep}
              grid={sweepGrid(sweep, metric, 'tuning')}
              metric={metric}
              title={`Days a value may be chosen on (${sweep.held_out.tuning_days})`}
              selected={selected}
              onSelect={setSelected}
            />
            {heldOutFrom ? (
              <HeatGrid
                sweep={sweep}
                grid={sweepGrid(sweep, metric, 'held_out')}
                metric={metric}
                title={`Days held out (${sweep.held_out.days})`}
                selected={selected}
                onSelect={setSelected}
              />
            ) : (
              <p className="text-sm text-muted-foreground">
                No day was held out: take a longer period before choosing a value from this.
              </p>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            A cell is tinted by where its figure stands among the others of its table; the figure is what counts. One
            turbo ({run.turbo.leverages[0]} index points for 1.00), no random entries.
          </p>
        </div>
      </Panel>

      {chosen && selected !== null && (
        <Panel
          title={cellName(sweep.axes, chosen).replace(/^./, (letter) => letter.toUpperCase())}
          description="The combination selected, on each part of the period."
          action={
            <Button
              size="sm"
              leftIcon={<Play className="size-3.5" />}
              isLoading={cellMutation.isPending}
              onClick={() => cellMutation.mutate(selected)}
            >
              Make it a backtest of its own
            </Button>
          }
        >
          {cellMutation.error && (
            <Alert tone="danger" role="alert" className="mb-4">
              <AlertDescription>{cellMutation.error.message}</AlertDescription>
            </Alert>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="py-2 pr-4" />
                  {PARTS.map(({ label }) => (
                    <th key={label} scope="col" className="py-2 pl-4 text-right text-xs font-medium text-muted-foreground">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {(
                  [
                    ['Days tested', (figures) => formatNumber(figures.days)],
                    ['Trades', (figures) => formatNumber(figures.trades)],
                    ['Winning trades', (figures) => formatPercent(figures.win_rate, false, 1)],
                    ['Per trade, on average', (figures) => <PnlValue value={figures.expectancy} kind="percent" />],
                    ['All days, one on the other', (figures) => <PnlValue value={figures.total_percent} kind="percent" />],
                    ['Worst day', (figures) => <PnlValue value={figures.worst_day} kind="percent" />],
                    ['Worst drawdown', (figures) => <PnlValue value={figures.worst_drawdown} kind="percent" />],
                  ] as [string, (figures: BacktestFigures) => React.ReactNode][]
                ).map(([label, read]) => (
                  <tr key={label}>
                    <th scope="row" className="py-2 pr-4 text-left font-normal text-muted-foreground">
                      {label}
                    </th>
                    {PARTS.map(({ part, label: partLabel }) => (
                      <Figure key={partLabel} figures={chosen[part]} read={read} />
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  )
}
