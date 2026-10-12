import { ActionBadge, PnlValue } from '@/components/status'
import { Badge } from '@/components/ui/badge'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import type { CsvColumn } from '@/lib/csv'
import { formatDateTime, formatMoney, formatNumber } from '@/lib/format'
import type { Order, Position } from '@/lib/types'

import { ClosePositionButton } from './ClosePositionButton'
import { LiveValue } from './LiveValue'

export const POSITION_CSV: CsvColumn<Position>[] = [
  { header: 'position_id', value: (row) => row.position_id },
  { header: 'status', value: (row) => row.position_status },
  { header: 'direction', value: (row) => row.action },
  { header: 'instrument', value: (row) => row.instrument_name },
  { header: 'symbol', value: (row) => row.instrument_symbol },
  { header: 'quantity', value: (row) => row.position_amount },
  { header: 'open_price', value: (row) => row.position_open_price },
  { header: 'close_price', value: (row) => row.position_close_price },
  { header: 'profit_loss', value: (row) => row.position_profit_loss },
  { header: 'performance_percent', value: (row) => row.position_total_performance_percent },
  { header: 'best_performance_percent', value: (row) => row.position_max_performance_percent },
  { header: 'opened_at', value: (row) => row.execution_time_open },
  { header: 'closed_at', value: (row) => row.execution_time_close },
  { header: 'close_reason', value: (row) => row.position_close_reason },
  { header: 'currency', value: (row) => row.instrument_currency },
]

export const ORDER_CSV: CsvColumn<Order>[] = [
  { header: 'order_id', value: (row) => row.order_id },
  { header: 'position_id', value: (row) => row.position_id },
  { header: 'time', value: (row) => row.order_time },
  { header: 'direction', value: (row) => row.action },
  { header: 'side', value: (row) => row.buy_sell },
  { header: 'type', value: (row) => row.order_type },
  { header: 'instrument', value: (row) => row.instrument_name },
  { header: 'quantity', value: (row) => row.order_amount },
  { header: 'price', value: (row) => row.instrument_price },
  { header: 'cost', value: (row) => row.order_cost },
  { header: 'currency', value: (row) => row.instrument_currency },
]

const DIRECTION_FILTER = [
  { label: 'Long', value: 'long' },
  { label: 'Short', value: 'short' },
]

function instrumentCell(name: string | null, symbol: string | null) {
  return (
    <div className="min-w-0">
      <p className="max-w-[22rem] truncate font-medium text-foreground">{name ?? '—'}</p>
      {symbol && <p className="text-xs text-muted-foreground">{symbol}</p>}
    </div>
  )
}

export type PositionView = 'open' | 'closed' | 'all'

interface PositionColumnOptions {
  /** The current time, for the age of live values. */
  now: number
  /** Whether the operator may close a position from here. */
  canClose: boolean
}

export function positionColumns(
  timeZone: string,
  view: PositionView,
  { now, canClose }: PositionColumnOptions,
): ColumnDef<Position>[] {
  const instrument: ColumnDef<Position> = {
    id: 'instrument',
    header: 'Instrument',
    accessorKey: 'instrument_name',
    sortable: true,
    cell: ({ row }) => instrumentCell(row.instrument_name, row.instrument_symbol),
  }
  const direction: ColumnDef<Position> = {
    id: 'action',
    header: 'Direction',
    accessorKey: 'action',
    sortable: true,
    filterable: true,
    filterOptions: DIRECTION_FILTER,
    cell: ({ row }) => <ActionBadge action={row.action} />,
  }
  const quantity: ColumnDef<Position> = {
    id: 'amount',
    header: 'Quantity',
    accessorKey: 'position_amount',
    sortable: true,
    align: 'right',
    cell: ({ row }) => formatNumber(row.position_amount),
  }
  const openPrice: ColumnDef<Position> = {
    id: 'open_price',
    header: 'Open price',
    accessorKey: 'position_open_price',
    sortable: true,
    align: 'right',
    cell: ({ row }) => formatMoney(row.position_open_price, row.instrument_currency),
  }
  const opened: ColumnDef<Position> = {
    id: 'opened',
    header: 'Opened',
    accessorKey: 'execution_time_open',
    sortable: true,
    cell: ({ row }) => formatDateTime(row.execution_time_open, timeZone),
  }
  const best: ColumnDef<Position> = {
    id: 'max_performance',
    // "So far" only while the position is open.
    header: view === 'open' ? 'Best so far' : 'Best level',
    accessorKey: 'position_max_performance_percent',
    sortable: true,
    align: 'right',
    cell: ({ row }) => <PnlValue value={row.position_max_performance_percent} kind="percent" />,
  }

  if (view === 'open') {
    const live: ColumnDef<Position> = {
      id: 'live',
      header: 'Now',
      accessorKey: 'live_performance_percent',
      sortable: true,
      align: 'right',
      cell: ({ row }) => <LiveValue position={row} now={now} />,
    }
    const close: ColumnDef<Position> = {
      id: 'close',
      header: '',
      align: 'right',
      cell: ({ row }) => <ClosePositionButton position={row} />,
    }
    return [instrument, direction, quantity, openPrice, opened, live, best, ...(canClose ? [close] : [])]
  }

  const status: ColumnDef<Position> = {
    id: 'status',
    header: 'Status',
    accessorKey: 'position_status',
    sortable: true,
    cell: ({ row }) =>
      row.position_status === 'Open' ? (
        <Badge tone="success" dot pulse>
          Open
        </Badge>
      ) : (
        <Badge>{row.position_status ?? '—'}</Badge>
      ),
  }
  const closePrice: ColumnDef<Position> = {
    id: 'close_price',
    header: 'Close price',
    accessorKey: 'position_close_price',
    sortable: true,
    align: 'right',
    cell: ({ row }) => formatMoney(row.position_close_price, row.instrument_currency),
  }
  const profitLoss: ColumnDef<Position> = {
    id: 'profit_loss',
    header: 'Profit / loss',
    accessorKey: 'position_profit_loss',
    sortable: true,
    align: 'right',
    cell: ({ row }) => <PnlValue value={row.position_profit_loss} kind="money" currency={row.instrument_currency} />,
  }
  const performance: ColumnDef<Position> = {
    id: 'performance',
    header: 'Performance',
    accessorKey: 'position_total_performance_percent',
    sortable: true,
    align: 'right',
    cell: ({ row }) => <PnlValue value={row.position_total_performance_percent} kind="percent" />,
  }
  const closed: ColumnDef<Position> = {
    id: 'closed',
    header: 'Closed',
    accessorKey: 'execution_time_close',
    sortable: true,
    cell: ({ row }) => formatDateTime(row.execution_time_close, timeZone),
  }
  const closeReason: ColumnDef<Position> = {
    id: 'close_reason',
    header: 'Close reason',
    accessorKey: 'position_close_reason',
    sortable: true,
    // The reasons are sentences written by the services ("Performance (Trailing
    // Stop: peak 9.1%, now 0.8% ...)"): on two lines at most, whole in the tooltip,
    // so that the column does not push the table off the page.
    cell: ({ row }) => (
      <span
        className="line-clamp-2 block min-w-[10rem] max-w-[22rem] whitespace-normal"
        title={row.position_close_reason ?? undefined}
      >
        {row.position_close_reason ?? '—'}
      </span>
    ),
  }
  // What a row is scanned for comes first and fits the page: when it closed,
  // what it made, why. The prices and the quantity are one click away, in the
  // column list and in the orders shown when a row is opened.
  const detail = (column: ColumnDef<Position>): ColumnDef<Position> => ({ ...column, defaultHidden: true })

  if (view === 'closed') {
    return [
      closed,
      instrument,
      direction,
      profitLoss,
      performance,
      best,
      closeReason,
      detail(quantity),
      detail(openPrice),
      detail(closePrice),
      detail(opened),
    ]
  }
  // Every row of the closed view has the same status: the column is for this one.
  return [
    instrument,
    direction,
    status,
    profitLoss,
    performance,
    best,
    opened,
    closed,
    closeReason,
    detail(quantity),
    detail(openPrice),
    detail(closePrice),
  ]
}

export function orderColumns(timeZone: string): ColumnDef<Order>[] {
  return [
    {
      id: 'time',
      header: 'Time',
      accessorKey: 'order_time',
      sortable: true,
      cell: ({ row }) => formatDateTime(row.order_time, timeZone),
    },
    {
      id: 'instrument',
      header: 'Instrument',
      accessorKey: 'instrument_name',
      sortable: true,
      cell: ({ row }) => instrumentCell(row.instrument_name, row.instrument_symbol),
    },
    {
      id: 'action',
      header: 'Direction',
      accessorKey: 'action',
      sortable: true,
      filterable: true,
      filterOptions: DIRECTION_FILTER,
      cell: ({ row }) => <ActionBadge action={row.action} />,
    },
    {
      id: 'side',
      header: 'Side',
      accessorKey: 'buy_sell',
      sortable: true,
      cell: ({ row }) => row.buy_sell ?? '—',
    },
    {
      id: 'type',
      header: 'Type',
      accessorKey: 'order_type',
      sortable: true,
      cell: ({ row }) => [row.order_type, row.order_kind].filter(Boolean).join(' · ') || '—',
    },
    {
      id: 'amount',
      header: 'Quantity',
      accessorKey: 'order_amount',
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatNumber(row.order_amount),
    },
    {
      id: 'price',
      header: 'Price',
      accessorKey: 'instrument_price',
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatMoney(row.instrument_price, row.instrument_currency),
    },
    {
      id: 'cost',
      header: 'Cost',
      accessorKey: 'order_cost',
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatMoney(row.order_cost, row.instrument_currency),
    },
    {
      id: 'order_id',
      header: 'Order',
      accessorKey: 'order_id',
      cell: ({ row }) => <span className="font-mono text-xs text-muted-foreground">{row.order_id}</span>,
    },
  ]
}
