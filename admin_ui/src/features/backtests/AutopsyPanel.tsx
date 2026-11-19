import { useQuery } from '@tanstack/react-query'
import { AdvancedDataTable, Alert, AlertDescription, Badge, formatNumber, Panel, QueryState } from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'

import { api } from '@/lib/api'
import type { BacktestAutopsyFeature } from '@/lib/types'

import { exitSentences, featureSaid } from './why'

const signed = (value: number | undefined) => (value === undefined ? '—' : `${value > 0 ? '+' : value < 0 ? '−' : ''}${formatNumber(Math.abs(value), 2)}`)

const COLUMNS: ColumnDef<BacktestAutopsyFeature>[] = [
  { id: 'label', header: 'Known at entry', accessorKey: 'label', sortable: true, cell: ({ row }) => <span className="font-medium">{row.label}</span> },
  {
    id: 'cut',
    header: 'Its cut',
    accessorFn: (row) => featureSaid(row),
    cell: ({ row }) => <span className="block max-w-80 whitespace-normal text-xs">{featureSaid(row)}</span>,
  },
  {
    id: 'chosen',
    header: 'First days',
    accessorFn: (row) => row.chosen?.difference ?? 0,
    sortable: true,
    cell: ({ row }) => <span className="tabular-nums">{signed(row.chosen?.difference)}</span>,
  },
  {
    id: 'later',
    header: 'Later days',
    accessorFn: (row) => row.tested?.difference ?? 0,
    sortable: true,
    cell: ({ row }) => <span className="tabular-nums">{signed(row.tested?.difference)}</span>,
  },
  { id: 'q', header: 'q', accessorFn: (row) => row.q ?? 1, sortable: true, cell: ({ row }) => <span className="tabular-nums">{row.q === null ? '—' : formatNumber(row.q, 2)}</span> },
  {
    id: 'held',
    header: 'Held',
    accessorFn: (row) => (row.held ? 'Held' : 'No'),
    sortable: true,
    filterable: true,
    cell: ({ row }) => (row.held ? <Badge tone="success">Held</Badge> : <span className="text-muted-foreground">—</span>),
  },
]

/**
 * What separated a run's winners from its losers, using only what was known at entry: each feature's cut chosen on
 * its first days and tested on the later ones (src/admin/trade_autopsy.py). What held is a hypothesis to test.
 */
export function AutopsyPanel({ runId }: { runId: string }) {
  const autopsyQuery = useQuery({ queryKey: ['backtest-autopsy', runId], queryFn: () => api.backtestAutopsy(runId), staleTime: Infinity })
  const found = autopsyQuery.data
  return (
    <Panel
      title="Winners and losers"
      description="What separated its winning trades from its losing ones, known at entry: each cut chosen on its first days, then tested on the later ones, with a bootstrap of whole days and a false discovery rate of 10 %. What held is a hypothesis to test in an experiment, never a result."
    >
      <QueryState isPending={autopsyQuery.isPending} error={autopsyQuery.error} hasData={!!found}>
        {found && (
          <div className="flex flex-col gap-4">
            {found.too_few ? (
              <p className="text-sm text-muted-foreground">Too few to test: {found.too_few}.</p>
            ) : found.held.length === 0 ? (
              <p className="text-sm">
                Nothing held: no cut chosen on {found.choose_days?.[0]} to {found.choose_days?.[1]} separated winners from losers again on the later days.
              </p>
            ) : (
              <Alert tone="neutral">
                <AlertDescription>
                  <ul className="flex flex-col gap-1">
                    {found.held.map((entry) => (
                      <li key={entry.name}>{entry.sentence}</li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
            )}
            {!found.too_few && (
              <AdvancedDataTable<BacktestAutopsyFeature>
                title="Each feature"
                data={found.features}
                columns={COLUMNS}
                getRowId={(row) => row.name}
                defaultPageSize={10}
                selectable={false}
              />
            )}
            <div className="text-sm">
              <p className="font-medium">The exits, read on all its days</p>
              <ul className="mt-1 flex flex-col gap-1 text-muted-foreground">
                {exitSentences(found.exits).map((sentence) => (
                  <li key={sentence}>{sentence}</li>
                ))}
              </ul>
              <p className="mt-1 text-xs text-muted-foreground">Hints of what to sweep: read on the same days, they prove nothing.</p>
            </div>
            <p className="text-xs text-muted-foreground">Left out, as known only after the entry: {found.left_out.join('; ')}.</p>
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
