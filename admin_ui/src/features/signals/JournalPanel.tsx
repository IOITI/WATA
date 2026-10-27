import { useQuery } from '@tanstack/react-query'
import { AdvancedDataTable, Badge, formatDateTime, formatNumber, FunnelChart, Panel, QueryState } from 'ioiti-ui'
import type { BadgeTone, ColumnDef } from 'ioiti-ui'
import { useMemo } from 'react'

import { ActionBadge } from '@/components/status'
import { PositionPeekLink } from '@/features/positions/PositionPeek'
import { api } from '@/lib/api'
import type { JournalEntry } from '@/lib/types'

import { journalFunnel } from './journalFunnel'

const OUTCOMES: Record<string, { label: string; tone: BadgeTone }> = {
  executed: { label: 'Executed', tone: 'success' },
  closed: { label: 'Positions closed', tone: 'success' },
  refused: { label: 'Refused', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' },
  ignored: { label: 'Nothing to do', tone: 'neutral' },
}

const OUTCOME_FILTER = Object.entries(OUTCOMES).map(([value, { label }]) => ({ value, label }))

/** What the trader did with each signal it received, and why when it did not trade it. */
export function JournalPanel({ timeZone }: { timeZone: string }) {
  const journalQuery = useQuery({ queryKey: ['journal'], queryFn: () => api.journal(), refetchInterval: 15_000 })
  const entries = journalQuery.data?.items
  const funnel = useMemo(() => (entries ? journalFunnel(entries) : null), [entries])

  const columns = useMemo<ColumnDef<JournalEntry>[]>(
    () => [
      {
        id: 'decided_at',
        header: 'Decided',
        accessorKey: 'decided_at',
        sortable: true,
        cell: ({ row }) => formatDateTime(row.decided_at, timeZone),
      },
      {
        id: 'action',
        header: 'Signal',
        accessorKey: 'action',
        sortable: true,
        cell: ({ row }) => <ActionBadge action={row.action} />,
      },
      { id: 'indice', header: 'Indice', accessorKey: 'indice', sortable: true },
      {
        id: 'outcome',
        header: 'Outcome',
        accessorKey: 'outcome',
        sortable: true,
        filterable: true,
        filterOptions: OUTCOME_FILTER,
        cell: ({ row }) => {
          const outcome = OUTCOMES[row.outcome] ?? { label: row.outcome, tone: 'neutral' as const }
          return <Badge tone={outcome.tone}>{outcome.label}</Badge>
        },
      },
      {
        id: 'reason',
        header: 'Detail',
        accessorKey: 'reason',
        cell: ({ row }) => (
          <span className="block max-w-[32rem] whitespace-normal">
            {row.reason ??
              (row.position_id ? (
                <>
                  Position{' '}
                  <PositionPeekLink id={row.position_id} className="font-mono underline decoration-border underline-offset-2">
                    {row.position_id}
                  </PositionPeekLink>
                </>
              ) : (
                '—'
              ))}
          </span>
        ),
      },
    ],
    [timeZone],
  )

  return (
    <QueryState isPending={journalQuery.isPending} error={journalQuery.error} hasData={!!entries}>
      {entries && funnel && (
        <Panel
          title="Where the entry signals went"
          description={`The long and short signals the trader decided, counted over the ${formatNumber(entries.length)} decisions read: from ${formatDateTime(funnel.from, timeZone)} to ${formatDateTime(funnel.to, timeZone)}.`}
        >
          <FunnelChart
            className="max-w-2xl"
            stages={funnel.stages}
            formatValue={(value) => formatNumber(value)}
            summaryLabel="of the entry signals bought"
            noun="Signals"
            ariaLabel="Where the entry signals the trader decided went, stage by stage"
          />
        </Panel>
      )}
      {entries && (
        <AdvancedDataTable<JournalEntry>
          title="Signal journal"
          searchPlaceholder="Search signal, reason…"
          emptyTitle="No signal handled yet"
          emptyDescription="The journal starts with the first signal the trader receives after this version is installed."
          data={entries}
          columns={columns}
          getRowId={(row) => row.signal_uuid}
          defaultPageSize={10}
          selectable={false}
        />
      )}
    </QueryState>
  )
}
