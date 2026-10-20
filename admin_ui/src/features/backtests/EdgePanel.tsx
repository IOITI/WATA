import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { formatNumber } from '@/lib/format'
import type { BacktestEdge } from '@/lib/types'
import { cn } from '@/lib/utils'

import { basisPoints, edgeRows, edgeSplits, edgeVerdict, horizonLabel } from './edge'
import type { EdgeSplit } from './edge'

const SPLITS: { id: EdgeSplit; label: string }[] = [
  { id: 'fate', label: 'Taken or not' },
  { id: 'direction', label: 'Direction' },
  { id: 'hour', label: 'Hour' },
  { id: 'volatility', label: 'Day volatility' },
  { id: 'day_type', label: 'Kind of day' },
  { id: 'gap', label: 'Gap' },
  { id: 'us_open', label: 'US open' },
  { id: 'news', label: 'Macro news' },
]

function Move({ value }: { value: number | null }) {
  return <span className={cn(value !== null && value > 0 && 'text-positive', value !== null && value < 0 && 'text-negative')}>{basisPoints(value)}</span>
}

/** The figures of each horizon, and a split of the mean moves. */
function EdgeTables({ edge }: { edge: BacktestEdge }) {
  const [split, setSplit] = useState<EdgeSplit>('fate')
  const rows = edgeRows(edge, split)
  const available = edgeSplits(edge)
  const splits = SPLITS.filter((entry) => available.includes(entry.id))
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full whitespace-nowrap text-sm">
          <thead>
            <tr className="text-xs text-muted-foreground">
              <th scope="col" className="p-2 text-left font-medium">
                After
              </th>
              <th scope="col" className="p-2 text-right font-medium">
                Signals
              </th>
              <th scope="col" className="p-2 text-right font-medium">
                Moved, on average
              </th>
              <th scope="col" className="p-2 text-right font-medium">
                Right
              </th>
              <th scope="col" className="p-2 text-right font-medium">
                Best, worst on the way
              </th>
              <th scope="col" className="p-2 text-right font-medium">
                t (apart)
              </th>
              <th scope="col" className="p-2 text-right font-medium">
                At random
              </th>
              <th scope="col" className="p-2 text-right font-medium">
                Draws beaten
              </th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {edge.horizons.map((row) => (
              <tr key={row.minutes} className="border-t border-border/50">
                <th scope="row" className="p-2 text-left font-medium">
                  {horizonLabel(row.minutes)}
                </th>
                <td className="p-2 text-right">{formatNumber(row.signals)}</td>
                <td className="p-2 text-right">
                  <Move value={row.mean_bp} />
                </td>
                <td className="p-2 text-right">{row.hit_rate === null ? '-' : `${formatNumber(row.hit_rate, 0)} %`}</td>
                <td className="p-2 text-right whitespace-nowrap">
                  {basisPoints(row.best_bp)}, {basisPoints(row.worst_bp)}
                </td>
                <td className="p-2 text-right whitespace-nowrap">
                  {row.t === null ? '-' : formatNumber(row.t, 1)} ({row.independent})
                </td>
                <td className="p-2 text-right">{basisPoints(row.random.mean_bp)}</td>
                <td className="p-2 text-right">{row.random.beaten === null ? '-' : `${formatNumber(row.random.beaten, 1)} %`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Moves split by">
        {splits.map((entry) => (
          <Button key={entry.id} size="sm" variant={entry.id === split ? 'primary' : 'outline'} aria-pressed={entry.id === split} onClick={() => setSplit(entry.id)}>
            {entry.label}
          </Button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full whitespace-nowrap text-sm" aria-label="Moves split">
          <thead>
            <tr className="text-xs text-muted-foreground">
              <th scope="col" className="p-2 text-left font-medium">
                {SPLITS.find((entry) => entry.id === split)?.label}
              </th>
              <th scope="col" className="p-2 text-right font-medium">
                Signals
              </th>
              {edge.horizons.map((row) => (
                <th key={row.minutes} scope="col" className="p-2 text-right font-medium">
                  {horizonLabel(row.minutes)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map((row) => (
              <tr key={row.label} className="border-t border-border/50">
                <th scope="row" className="p-2 text-left font-medium">
                  {row.label}
                </th>
                <td className="p-2 text-right">{formatNumber(row.signals)}</td>
                {row.moves.map((value, index) => (
                  <td key={edge.horizons[index].minutes} className="p-2 text-right">
                    <Move value={value} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

/** An edge measured: a run made before it was answers with nothing. */
function isEdge(value: unknown): value is BacktestEdge {
  return typeof value === 'object' && value !== null && Array.isArray((value as BacktestEdge).horizons)
}

/**
 * Whether the entries point the right way before WATA's exits have a say: how
 * the asset moved after each entry signal, taken or not, against random moments
 * of the same hours.
 */
export function EdgePanel({ runId }: { runId: string }) {
  const edgeQuery = useQuery({ queryKey: ['backtest-edge', runId], queryFn: () => api.backtestEdge(runId), staleTime: Infinity })
  const edge = isEdge(edgeQuery.data) ? edgeQuery.data : null
  const verdict = edge ? edgeVerdict(edge) : null
  return (
    <Panel
      title="Edge of the entries"
      description="How the asset moved after each entry signal, taken or not, in the signal's direction, in basis points of its price; beside moments drawn at random in the same hours of the same days. A trade's result also owes to the turbo, the stop and the day: this does not."
    >
      <QueryState isPending={edgeQuery.isPending} error={edgeQuery.error} hasData={!!edgeQuery.data}>
        {!edge || !verdict ? (
          <p className="text-sm text-muted-foreground">Not measured: this run was made before the edge of its entries was.</p>
        ) : (
          <div className="flex flex-col gap-4">
            <Alert tone={verdict.tone === 'edge' ? 'success' : verdict.tone === 'none' ? 'warning' : 'neutral'}>
              <AlertDescription>{verdict.sentence}</AlertDescription>
            </Alert>
            {edge.horizons.some((row) => row.signals > 0) && <EdgeTables edge={edge} />}
            {edge.unpriced > 0 && (
              <p className="text-xs text-muted-foreground">
                {edge.unpriced} signal{edge.unpriced === 1 ? '' : 's'} came when the asset had no price: left out.
              </p>
            )}
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
