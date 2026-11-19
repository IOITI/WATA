import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AdvancedDataTable, Alert, AlertDescription, Badge, Button, formatNumber, Hint, Panel, QueryState } from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { Check, ListPlus } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { hypothesisFromAutopsy } from '@/features/agent/queueModel'
import { api } from '@/lib/api'
import type { BacktestAutopsy, BacktestAutopsyFeature } from '@/lib/types'

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
/** What held, filed as a hypothesis of the research queue at the operator's click: the autopilot tries it later. */
function FileHypothesis({ held, autopsy, run }: {
  held: BacktestAutopsy['held'][number]
  autopsy: BacktestAutopsy
  run: { id: string; name: string | null; family: string | null }
}) {
  const queryClient = useQueryClient()
  const [filed, setFiled] = useState(false)
  const file = useMutation({
    mutationFn: () => api.addHypothesis(hypothesisFromAutopsy(held, autopsy, run)),
    onSuccess: () => {
      setFiled(true)
      void queryClient.invalidateQueries({ queryKey: ['research-queue'] })
    },
  })
  if (filed) {
    return (
      <Link to="/agent/queue" className="inline-flex items-center gap-1 text-xs underline underline-offset-2">
        <Check aria-hidden className="size-3.5" /> In the research queue
      </Link>
    )
  }
  return (
    <Hint content={file.error ? file.error.message : 'A hypothesis to test on other days, in an experiment of its own: the research autopilot takes it in its turn'}>
      <Button variant="ghost" size="sm" isLoading={file.isPending} leftIcon={<ListPlus className="size-3.5" />}
        aria-label={`Add “${held.said}” to the research queue`} onClick={() => file.mutate()}>
        Add to the research queue
      </Button>
    </Hint>
  )
}

export function AutopsyPanel({ runId, runName = null, family = null }: { runId: string; runName?: string | null; family?: string | null }) {
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
                  <ul className="flex flex-col gap-2">
                    {found.held.map((entry) => (
                      <li key={entry.name} className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                        <span>{entry.sentence}</span>
                        <FileHypothesis held={entry} autopsy={found} run={{ id: runId, name: runName, family }} />
                      </li>
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
