import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, Button, cn, formatNumber, Panel, QueryState, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from 'ioiti-ui'
import { useState } from 'react'

import { api } from '@/lib/api'
import type { BacktestEdge } from '@/lib/types'

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
      <Table density="dense">
        <TableHead>
          <TableRow>
            <TableHeaderCell scope="col">
              After
            </TableHeaderCell>
            <TableHeaderCell numeric scope="col">
              Signals
            </TableHeaderCell>
            <TableHeaderCell numeric scope="col">
              Moved, on average
            </TableHeaderCell>
            <TableHeaderCell numeric scope="col">
              Right
            </TableHeaderCell>
            <TableHeaderCell numeric scope="col">
              Best, worst on the way
            </TableHeaderCell>
            <TableHeaderCell numeric scope="col">
              t (apart)
            </TableHeaderCell>
            <TableHeaderCell numeric scope="col">
              At random
            </TableHeaderCell>
            <TableHeaderCell numeric scope="col">
              Draws beaten
            </TableHeaderCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {edge.horizons.map((row) => (
            <TableRow key={row.minutes} className="border-border/50">
              <TableHeaderCell scope="row">
                {horizonLabel(row.minutes)}
              </TableHeaderCell>
              <TableCell numeric>{formatNumber(row.signals)}</TableCell>
              <TableCell numeric>
                <Move value={row.mean_bp} />
              </TableCell>
              <TableCell numeric>{row.hit_rate === null ? '-' : `${formatNumber(row.hit_rate, 0)} %`}</TableCell>
              <TableCell numeric className="whitespace-nowrap">
                {basisPoints(row.best_bp)}, {basisPoints(row.worst_bp)}
              </TableCell>
              <TableCell numeric className="whitespace-nowrap">
                {row.t === null ? '-' : formatNumber(row.t, 1)} ({row.independent})
              </TableCell>
              <TableCell numeric>{basisPoints(row.random.mean_bp)}</TableCell>
              <TableCell numeric>{row.random.beaten === null ? '-' : `${formatNumber(row.random.beaten, 1)} %`}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Moves split by">
        {splits.map((entry) => (
          <Button key={entry.id} size="sm" variant={entry.id === split ? 'primary' : 'outline'} aria-pressed={entry.id === split} onClick={() => setSplit(entry.id)}>
            {entry.label}
          </Button>
        ))}
      </div>
      <Table density="dense" aria-label="Moves split">
        <TableHead>
          <TableRow>
            <TableHeaderCell scope="col">
              {SPLITS.find((entry) => entry.id === split)?.label}
            </TableHeaderCell>
            <TableHeaderCell numeric scope="col">
              Signals
            </TableHeaderCell>
            {edge.horizons.map((row) => (
              <TableHeaderCell numeric key={row.minutes} scope="col">
                {horizonLabel(row.minutes)}
              </TableHeaderCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.label} className="border-border/50">
              <TableHeaderCell scope="row">
                {row.label}
              </TableHeaderCell>
              <TableCell numeric>{formatNumber(row.signals)}</TableCell>
              {row.moves.map((value, index) => (
                <TableCell numeric key={edge.horizons[index].minutes}>
                  <Move value={value} />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
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
