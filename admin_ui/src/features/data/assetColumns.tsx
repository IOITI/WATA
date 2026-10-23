import { AlertTriangle } from 'lucide-react'
import { Link } from 'react-router'

import { Hint } from '@/components/Hint'
import { Badge } from '@/components/ui/badge'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { valueDecimals } from '@/features/prices/chartData'
import { formatDateTime, formatNumber } from '@/lib/format'
import type { AssetStatus } from '@/lib/types'

import { assetHref, PROVIDER_LABELS, STATUS_LABELS, TYPE_LABELS } from './assets'
import type { AssetRow } from './assets'

const options = (labels: Record<string, string>) => Object.entries(labels).map(([value, label]) => ({ value, label }))

const STATUS_TONE: Record<AssetStatus, 'success' | 'neutral'> = {
  recording: 'success',
  ended: 'neutral',
  downloaded: 'neutral',
}

function isoOf(epoch: number | null): string | null {
  return epoch === null ? null : new Date(epoch * 1000).toISOString()
}

function lastPrice(row: AssetRow): string {
  if (row.last_bid === null) return '—'
  const decimals = valueDecimals([row.last_bid, row.last_ask])
  // An index has no bid and ask of its own: one level.
  if (row.last_ask === null || row.last_ask === row.last_bid) return formatNumber(row.last_bid, decimals)
  return `${formatNumber(row.last_bid, decimals)} / ${formatNumber(row.last_ask, decimals)}`
}

/**
 * The columns of the Assets View. `flags` says, by ref, what is missing today
 * from the turbos Saxo left without a quote: told by a shape and by its text,
 * not by its colour alone.
 */
export function assetColumns(timeZone: string, flags: ReadonlyMap<string, string>): ColumnDef<AssetRow>[] {
  return [
    {
      id: 'name',
      header: 'Asset',
      accessorKey: 'name',
      sortable: true,
      cell: ({ row }) => {
        const flag = flags.get(row.ref)
        return (
          <div className="flex min-w-0 items-center gap-1.5">
            <Link
              to={assetHref(row)}
              className="truncate font-medium text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground"
            >
              {row.name}
            </Link>
            {flag && (
              <Hint content={flag} wrap className="shrink-0">
                <span className="text-warning">
                  <AlertTriangle aria-hidden className="size-3.5" />
                  <span className="sr-only">{flag}</span>
                </span>
              </Hint>
            )}
          </div>
        )
      },
    },
    {
      id: 'type',
      header: 'Type',
      accessorKey: 'type',
      sortable: true,
      filterable: true,
      filterOptions: options(TYPE_LABELS),
      cell: ({ row }) => TYPE_LABELS[row.type],
    },
    {
      id: 'provider',
      header: 'Provider',
      accessorKey: 'provider',
      sortable: true,
      filterable: true,
      filterOptions: options(PROVIDER_LABELS),
      cell: ({ row }) => PROVIDER_LABELS[row.provider],
    },
    { id: 'indice', header: 'Indice', accessorKey: 'indice', sortable: true, cell: ({ row }) => row.indice ?? '—' },
    {
      id: 'barrier',
      header: 'Knock-out',
      accessorKey: 'barrier',
      sortable: true,
      align: 'right',
      cell: ({ row }) => (row.barrier === null ? '—' : new Intl.NumberFormat('en-GB').format(row.barrier)),
    },
    { id: 'issuer', header: 'Issuer', accessorKey: 'issuer', sortable: true, cell: ({ row }) => row.issuer ?? '—' },
    { id: 'last_price', header: 'Last price', accessorKey: 'last_bid', align: 'right', cell: ({ row }) => lastPrice(row) },
    {
      id: 'last_t',
      header: 'Last data',
      accessorKey: 'last_t',
      sortable: true,
      cell: ({ row }) => formatDateTime(isoOf(row.last_t), timeZone, false),
    },
    {
      id: 'status',
      header: 'Status',
      accessorKey: 'status',
      sortable: true,
      filterable: true,
      filterOptions: options(STATUS_LABELS),
      cell: ({ row }) => (
        <Badge tone={STATUS_TONE[row.status]} dot={row.status === 'recording'} pulse={row.status === 'recording'}>
          {STATUS_LABELS[row.status]}
        </Badge>
      ),
    },
    {
      id: 'first_t',
      header: 'First data',
      accessorKey: 'first_t',
      sortable: true,
      defaultHidden: true,
      cell: ({ row }) => formatDateTime(isoOf(row.first_t), timeZone, false),
    },
    { id: 'symbol', header: 'Symbol', accessorKey: 'symbol', defaultHidden: true, cell: ({ row }) => row.symbol ?? '—' },
    { id: 'uic', header: 'UIC', accessorKey: 'uic', defaultHidden: true, cell: ({ row }) => row.uic ?? '—' },
  ]
}
