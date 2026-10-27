import { useQuery } from '@tanstack/react-query'
import { AdvancedDataTable, ExportButton, formatDateTime, PageHeader, QueryState } from 'ioiti-ui'
import type { ColumnDef, CsvColumn } from 'ioiti-ui'
import { useMemo } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { AuditEntry } from '@/lib/types'

const AUDIT_CSV: CsvColumn<AuditEntry>[] = [
  { header: 'at', value: (row) => row.at },
  { header: 'action', value: (row) => row.action },
  { header: 'detail', value: (row) => row.detail },
  { header: 'ip', value: (row) => row.ip },
]

export function AuditPage() {
  const { timezone } = useSession()
  const auditQuery = useQuery({ queryKey: ['audit'], queryFn: api.audit, refetchInterval: 30_000 })
  const entries = auditQuery.data?.items

  const columns = useMemo<ColumnDef<AuditEntry>[]>(
    () => [
      {
        id: 'at',
        header: 'When',
        accessorKey: 'at',
        sortable: true,
        cell: ({ row }) => formatDateTime(row.at, timezone),
      },
      {
        id: 'action',
        header: 'Action',
        accessorKey: 'action',
        sortable: true,
        // Stored in lower case (and sent to Telegram that way): a sentence here.
        cell: ({ row }) => (
          <span className="font-medium text-foreground">{row.action.charAt(0).toUpperCase() + row.action.slice(1)}</span>
        ),
      },
      {
        id: 'detail',
        header: 'Detail',
        accessorKey: 'detail',
        cell: ({ row }) => <span className="whitespace-normal">{row.detail || '—'}</span>,
      },
      {
        id: 'ip',
        header: 'From',
        accessorKey: 'ip',
        sortable: true,
        cell: ({ row }) => <span className="font-mono text-xs text-muted-foreground">{row.ip}</span>,
      },
    ],
    [timezone],
  )

  return (
    <>
      <PageHeader
        title="Audit trail"
        description={`Every sign-in and every action taken through this interface. Times are in ${timezone}.`}
        actions={<ExportButton name="wata-audit" rows={entries ?? []} columns={AUDIT_CSV} />}
      />
      <QueryState isPending={auditQuery.isPending} error={auditQuery.error} hasData={!!entries}>
        {entries && (
          <AdvancedDataTable<AuditEntry>
            title="Actions"
            searchPlaceholder="Search action, detail, address…"
            emptyTitle="Nothing recorded yet"
            emptyDescription="Sign-ins and actions taken from this interface are listed here."
            data={entries}
            columns={columns}
            getRowId={(row) => `${row.at}-${row.action}`}
            defaultPageSize={20}
            selectable={false}
          />
        )}
      </QueryState>
    </>
  )
}
