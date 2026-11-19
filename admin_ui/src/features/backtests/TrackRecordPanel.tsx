import { AdvancedDataTable, KpiStrip, Panel } from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'

import type { TrackRecord, TrackRecordGroup } from '@/lib/types'

import { recordSentence } from './experiments'

type Row = TrackRecordGroup & { name: string }

function columns(first: string): ColumnDef<Row>[] {
  return [
    { id: 'name', header: first, accessorKey: 'name', sortable: true, cell: ({ row }) => <span className="font-medium">{row.name}</span> },
    { id: 'trials', header: 'Trials', accessorKey: 'trials', sortable: true, cell: ({ row }) => <span className="tabular-nums">{row.trials}</span> },
    {
      id: 'kept',
      header: 'Kept',
      accessorKey: 'kept',
      sortable: true,
      cell: ({ row }) => (
        <span className="tabular-nums">
          {row.kept} of {row.experiments}
        </span>
      ),
    },
    {
      id: 'held',
      header: 'Held on later days',
      accessorFn: (row) => row.held_share ?? -1,
      sortable: true,
      cell: ({ row }) => (
        <div className="min-w-0 max-w-[28rem] whitespace-normal text-xs">
          {row.held_share !== null && <span className="text-sm font-medium tabular-nums">{row.held_share} % · </span>}
          {recordSentence(row)}
        </div>
      ),
    },
  ]
}

/**
 * The research's track record: how the conclusions of each family of ideas, and of each maker, held on the days after
 * them. A family that keeps conclusions which then break has its luck said here, before its next try.
 */
export function TrackRecordPanel({ record }: { record: TrackRecord }) {
  const total = record.total
  const judged = total.forward.held + total.forward.watch + total.forward.broken
  return (
    <div className="flex flex-col gap-6">
      <Panel
        title="Track record"
        description="Each kept conclusion is judged on the days after it, which no run of its family had seen: the only test no choice was fitted to. A share is given from five judged checks."
      >
        <KpiStrip
          columns={4}
          items={[
            { label: 'Experiments', value: total.experiments, hint: `${total.trials} configurations tried` },
            { label: 'Kept', value: total.kept, hint: `${total.kept_against_bar} against the bar` },
            { label: 'Judged on later days', value: judged, hint: `${total.forward.active} running, ${total.forward.too_early} too early` },
            {
              label: 'Held',
              value: total.held_share === null ? '—' : `${total.held_share} %`,
              hint: `${total.forward.held} held, ${total.forward.watch} on watch, ${total.forward.broken} broken`,
            },
          ]}
        />
      </Panel>
      <AdvancedDataTable<Row>
        title="By family of ideas"
        emptyTitle="No experiment yet"
        data={record.families.map((entry) => ({ ...entry, name: entry.family }))}
        columns={columns('Family')}
        getRowId={(row) => row.name}
        defaultPageSize={10}
        selectable={false}
      />
      <AdvancedDataTable<Row>
        title="By maker"
        emptyTitle="No experiment yet"
        data={record.makers.map((entry) => ({ ...entry, name: entry.maker }))}
        columns={columns('Maker')}
        getRowId={(row) => row.name}
        defaultPageSize={10}
        selectable={false}
      />
    </div>
  )
}
