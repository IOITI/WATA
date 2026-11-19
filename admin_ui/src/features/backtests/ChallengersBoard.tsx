import { useQuery } from '@tanstack/react-query'
import { AdvancedDataTable, Badge, formatDay, formatNumber, formatPercent, Panel, QueryState } from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { Link } from 'react-router'

import { api } from '@/lib/api'
import type { ChallengerLine } from '@/lib/types'

import { CHALLENGER_SAID, challengerSentence, forwardCheckPath } from './experiments'

const percent = (value: number) => formatPercent(value, true)

const COLUMNS: ColumnDef<ChallengerLine>[] = [
  {
    id: 'conclusion',
    header: 'Conclusion',
    accessorFn: (row) => `${row.title ?? ''} ${row.family ?? ''}`,
    sortable: true,
    cell: ({ row }) => (
      <div className="min-w-0 max-w-[24rem] whitespace-normal">
        <Link to={forwardCheckPath(row.check)} className="font-medium underline-offset-2 hover:underline">
          {row.title ?? row.experiment ?? row.check}
        </Link>
        <p className="text-xs text-muted-foreground">
          {row.family}
          {row.state === 'ended' && ' · its check ended'}
        </p>
      </div>
    ),
  },
  {
    id: 'days',
    header: 'Days both traded',
    accessorKey: 'days',
    sortable: true,
    cell: ({ row }) => (
      <div className="whitespace-nowrap">
        <span className="tabular-nums">{formatNumber(row.days)}</span>
        {row.first && row.last && (
          <p className="text-xs text-muted-foreground">
            {formatDay(row.first)} – {formatDay(row.last)}
          </p>
        )}
      </div>
    ),
  },
  {
    id: 'better',
    header: 'Better, worse',
    accessorFn: (row) => row.better_days - row.worse_days,
    sortable: true,
    cell: ({ row }) => (
      <div className="whitespace-nowrap tabular-nums">
        {row.better_days}, {row.worse_days}
        {row.sign_p !== null && <p className="text-xs text-muted-foreground">sign test p = {formatNumber(row.sign_p, 2)}</p>}
      </div>
    ),
  },
  {
    id: 'results',
    header: 'It, live',
    accessorFn: (row) => row.challenger_percent - row.champion_percent,
    sortable: true,
    cell: ({ row }) => (
      <div className="whitespace-nowrap tabular-nums">
        {percent(row.challenger_percent)}, {percent(row.champion_percent)}
        {row.interval && (
          <p className="text-xs text-muted-foreground">
            a day: {percent(row.interval[0])} to {percent(row.interval[1])}
          </p>
        )}
      </div>
    ),
  },
  {
    id: 'verdict',
    header: 'Against live',
    accessorFn: (row) => CHALLENGER_SAID[row.verdict].label,
    sortable: true,
    filterable: true,
    cell: ({ row }) => <Badge tone={CHALLENGER_SAID[row.verdict].tone}>{CHALLENGER_SAID[row.verdict].label}</Badge>,
  },
]

/**
 * Each forward check's candidate beside the live configuration (the signals WATA received, traded on paper each
 * evening under the live settings) on the days both were traded: whether it would do better than what WATA trades now
 * (src/admin/challengers.py).
 */
export function ChallengersBoard() {
  const checksQuery = useQuery({ queryKey: ['forward-checks'], queryFn: api.forwardChecks, refetchInterval: 60_000 })
  const board = checksQuery.data?.board ?? []
  return (
    <QueryState isPending={checksQuery.isPending} error={checksQuery.error} hasData={!!checksQuery.data}>
      <Panel
        title="Against the live configuration"
        description="Each kept conclusion's candidate on its later days, set against the live configuration traded on paper on the same days: which day each did better, an exact sign test of those days, a 90 % interval of the mean daily difference with the days drawn again, and both compounded. A few days say little."
      >
        <div className="flex flex-col gap-3">
          {board.length > 0 && (
            <ul className="flex flex-col gap-1 text-sm">
              {board.slice(0, 3).map((line) => (
                <li key={line.check}>
                  <span className="font-medium">{line.title ?? line.check}</span>: {challengerSentence(line)}
                </li>
              ))}
            </ul>
          )}
          <AdvancedDataTable<ChallengerLine>
            title="Challengers"
            searchPlaceholder="Search a conclusion…"
            emptyTitle="No candidate judged on later days yet"
            emptyDescription="Once a forward check has judged a day its candidate and the live configuration both traded on paper, it is set against it here."
            data={board}
            columns={COLUMNS}
            getRowId={(row) => row.check}
            defaultPageSize={10}
            selectable={false}
          />
        </div>
      </Panel>
    </QueryState>
  )
}
