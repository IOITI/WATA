import { useMemo } from 'react'
import { Link } from 'react-router'

import { ExportButton } from '@/components/ExportButton'
import { ActionBadge, PnlValue } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Badge } from '@/components/ui/badge'
import { formatDay, formatMoney, formatNumber, formatPercent, formatTime } from '@/lib/format'
import { isPlainClick } from '@/lib/links'
import type { CsvColumn } from '@/lib/csv'
import type { BacktestDay, BacktestSignal, BacktestTrade, BacktestTurbos } from '@/lib/types'

import { DAY_STATUS_LABEL, dayStatusLabel, FATE_LABEL, heldFor, ruleLabel } from './model'
import { FateBadge } from './parts'

const SOURCE_LABEL: Record<BacktestSignal['source'], string> = { pine: 'Script', journal: 'Received', schedule: 'Day close' }

function signalTime(signal: BacktestSignal): string | null {
  return signal.bar_closed_at ?? signal.received_at ?? signal.arrived_at
}

// ── days ──

export function DaysTable({ runId, days, turbos = 'recorded' }: { runId: string; days: BacktestDay[]; turbos?: BacktestTurbos }) {
  const columns = useMemo<ColumnDef<BacktestDay>[]>(
    () => [
      {
        id: 'day',
        header: 'Day',
        accessorKey: 'day',
        sortable: true,
        cell: ({ row }) =>
          row.status === 'closed' ? (
            formatDay(row.day)
          ) : (
            <Link to={`/backtests/${runId}/days/${row.day}`} className="font-medium underline-offset-4 hover:underline">
              {formatDay(row.day)}
            </Link>
          ),
      },
      {
        id: 'status',
        header: 'Traded',
        accessorFn: (row) => dayStatusLabel(row.status, turbos),
        sortable: true,
        filterable: true,
        filterOptions: (Object.keys(DAY_STATUS_LABEL) as BacktestDay['status'][]).map((status) => ({
          value: dayStatusLabel(status, turbos),
          label: dayStatusLabel(status, turbos),
        })),
        cell: ({ row }) => <Badge tone={row.status === 'tested' ? 'success' : 'neutral'}>{dayStatusLabel(row.status, turbos)}</Badge>,
      },
      {
        id: 'recorded',
        header: turbos === 'modelled' ? 'Turbos made up' : 'Turbos recorded',
        accessorFn: (row) => (row.recorded ? row.recorded.long + row.recorded.short : 0),
        sortable: true,
        align: 'right',
        cell: ({ row }) =>
          row.recorded ? (
            <span className="tabular-nums" title={`${formatNumber(row.recorded.minutes)} min listened`}>
              {row.recorded.long} long, {row.recorded.short} short
            </span>
          ) : (
            '-'
          ),
      },
      {
        id: 'signals',
        header: 'Signals',
        accessorFn: (row) => Object.values(row.signals).reduce((sum: number, count) => sum + (count ?? 0), 0),
        sortable: true,
        align: 'right',
        cell: ({ row }) => (
          <span className="tabular-nums" title={Object.entries(row.signals).filter(([, count]) => count).map(([fate, count]) => `${count} ${FATE_LABEL[fate as keyof typeof FATE_LABEL]}`).join(', ')}>
            {Object.values(row.signals).reduce((sum: number, count) => sum + (count ?? 0), 0)}
          </span>
        ),
      },
      { id: 'trades', header: 'Trades', accessorKey: 'trades', sortable: true, align: 'right' },
      {
        id: 'day_percent',
        header: 'Day',
        accessorKey: 'day_percent',
        sortable: true,
        align: 'right',
        cell: ({ row }) => <PnlValue value={row.day_percent} kind="percent" />,
      },
      {
        id: 'profit_loss',
        header: 'In money',
        accessorKey: 'profit_loss',
        sortable: true,
        align: 'right',
        cell: ({ row }) => <PnlValue value={row.profit_loss} kind="money" />,
      },
      {
        id: 'equity',
        header: 'Account',
        accessorKey: 'equity',
        sortable: true,
        align: 'right',
        cell: ({ row }) => formatMoney(row.equity, 'EUR'),
      },
    ],
    [runId, turbos],
  )
  const csv: CsvColumn<BacktestDay>[] = [
    { header: 'day', value: (row) => row.day },
    { header: 'status', value: (row) => row.status },
    { header: 'long_turbos', value: (row) => row.recorded?.long },
    { header: 'short_turbos', value: (row) => row.recorded?.short },
    { header: 'recorder_minutes', value: (row) => row.recorded?.minutes },
    { header: 'signals', value: (row) => Object.values(row.signals).reduce((sum: number, count) => sum + (count ?? 0), 0) },
    { header: 'entries', value: (row) => row.entries },
    { header: 'trades', value: (row) => row.trades },
    { header: 'day_percent', value: (row) => row.day_percent },
    { header: 'profit_loss', value: (row) => row.profit_loss },
    { header: 'equity', value: (row) => row.equity },
  ]
  return (
    <div className="flex flex-col gap-2">
      <AdvancedDataTable<BacktestDay>
        title="Days"
        searchPlaceholder="Search a day…"
        emptyTitle="No day"
        data={days}
        columns={columns}
        getRowId={(row) => row.day}
        defaultPageSize={10}
        selectable={false}
      />
      <div className="flex justify-end">
        <ExportButton name={`wata-backtest-${runId}-days`} rows={days} columns={csv} />
      </div>
    </div>
  )
}

// ── trades ──

const TRADE_CSV: CsvColumn<BacktestTrade>[] = [
  { header: 'trade', value: (row) => row.id },
  { header: 'day', value: (row) => row.day },
  { header: 'direction', value: (row) => row.direction },
  { header: 'signal', value: (row) => row.signal_id },
  { header: 'confidence', value: (row) => row.confidence },
  { header: 'turbo_symbol', value: (row) => row.turbo.symbol },
  { header: 'turbo_description', value: (row) => row.turbo.description },
  { header: 'issuer', value: (row) => row.turbo.issuer },
  { header: 'knock_out_level', value: (row) => row.turbo.barrier },
  { header: 'uic', value: (row) => row.turbo.uic },
  { header: 'bought_at', value: (row) => row.entry.filled_at },
  { header: 'ask', value: (row) => row.entry.price },
  { header: 'sold_at', value: (row) => row.exit.filled_at },
  { header: 'bid', value: (row) => row.exit.bid },
  { header: 'units', value: (row) => row.units },
  { header: 'cost', value: (row) => row.cost },
  { header: 'proceeds', value: (row) => row.proceeds },
  { header: 'commission', value: (row) => row.commission },
  { header: 'profit_loss', value: (row) => row.profit_loss },
  { header: 'performance_percent', value: (row) => row.performance_percent },
  { header: 'best_percent', value: (row) => row.max_performance_percent },
  { header: 'worst_percent', value: (row) => row.min_performance_percent },
  { header: 'held_seconds', value: (row) => row.holding_seconds },
  { header: 'scale_percent', value: (row) => row.scale_percent },
  { header: 'sold_on', value: (row) => row.exit.reason },
  { header: 'asset_at_entry', value: (row) => row.entry.asset_price },
  { header: 'asset_at_exit', value: (row) => row.exit.asset_price },
]

/** A trade's address: its day, with it opened. */
function tradePath(runId: string, day: string, tradeId: number): string {
  return `/backtests/${runId}/days/${day}?trade=${tradeId}`
}

export function TradesTable({
  runId,
  trades,
  timezone,
  onShow,
  withDay = true,
}: {
  runId: string
  trades: BacktestTrade[]
  timezone: string
  /** On a day's page: what a click on a trade does there. Opened in another tab, a trade's link is its day. */
  onShow?: (trade: BacktestTrade) => void
  withDay?: boolean
}) {
  const columns = useMemo<ColumnDef<BacktestTrade>[]>(
    () => [
      {
        id: 'trade',
        header: 'Trade',
        accessorFn: (row) => row.id,
        sortable: true,
        cell: ({ row }) => (
          <Link
            to={tradePath(runId, row.day, row.id)}
            className="font-medium underline-offset-4 hover:underline"
            onClick={(event) => {
              if (!onShow || !isPlainClick(event)) return
              event.preventDefault()
              onShow(row)
            }}
          >
            #{row.id}
          </Link>
        ),
      },
      ...(withDay
        ? [{ id: 'day', header: 'Day', accessorKey: 'day' as const, sortable: true, cell: ({ row }: { row: BacktestTrade }) => formatDay(row.day) }]
        : []),
      {
        id: 'direction',
        header: 'Direction',
        accessorKey: 'direction',
        sortable: true,
        filterable: true,
        filterOptions: [
          { value: 'long', label: 'Long' },
          { value: 'short', label: 'Short' },
        ],
        cell: ({ row }) => <ActionBadge action={row.direction} />,
      },
      {
        id: 'turbo',
        header: 'Turbo',
        accessorFn: (row) => `${row.turbo.symbol ?? ''} ${row.turbo.issuer ?? ''} ${row.turbo.description ?? ''}`,
        cell: ({ row }) => (
          <div className="min-w-0 max-w-64 whitespace-normal">
            <p className="font-medium">{row.turbo.symbol ?? row.turbo.instrument_id}</p>
            <p className="text-xs text-muted-foreground">
              {row.turbo.issuer} · knock-out {formatNumber(row.turbo.barrier)}
            </p>
          </div>
        ),
      },
      {
        id: 'bought',
        header: 'Bought',
        accessorFn: (row) => row.entry.filled_at,
        sortable: true,
        cell: ({ row }) => (
          <div className="tabular-nums">
            <p>{formatTime(row.entry.filled_at, timezone)}</p>
            <p className="text-xs text-muted-foreground">
              {formatNumber(row.units)} × {row.entry.price}
            </p>
          </div>
        ),
      },
      {
        id: 'sold',
        header: 'Sold',
        accessorFn: (row) => row.exit.filled_at,
        sortable: true,
        cell: ({ row }) => (
          <div className="tabular-nums">
            <p>{formatTime(row.exit.filled_at, timezone)}</p>
            <p className="text-xs text-muted-foreground">at {row.exit.bid}, after {heldFor(row.holding_seconds)}</p>
          </div>
        ),
      },
      {
        id: 'result',
        header: 'Result',
        accessorKey: 'performance_percent',
        sortable: true,
        align: 'right',
        cell: ({ row }) => (
          <div className="text-right">
            <PnlValue value={row.performance_percent} kind="percent" />
            <p className="text-xs">
              <PnlValue value={row.profit_loss} kind="money" />
            </p>
          </div>
        ),
      },
      {
        id: 'range',
        header: 'Best, worst',
        accessorKey: 'max_performance_percent',
        sortable: true,
        align: 'right',
        cell: ({ row }) => (
          <span className="tabular-nums text-xs">
            {formatPercent(row.max_performance_percent, true)}, {formatPercent(row.min_performance_percent, true)}
          </span>
        ),
      },
      {
        id: 'exit',
        header: 'Sold on',
        accessorFn: (row) => row.exit.group,
        sortable: true,
        filterable: true,
        filterOptions: [...new Set(trades.map((trade) => trade.exit.group))].map((group) => ({ value: group, label: group })),
        cell: ({ row }) => (
          <span className="block max-w-56 whitespace-normal" title={row.exit.reason}>
            {row.exit.group}
            {row.notes.length > 0 && <span className="block text-xs text-warning">{row.notes.length} note{row.notes.length === 1 ? '' : 's'}</span>}
          </span>
        ),
      },
    ],
    [runId, timezone, onShow, withDay, trades],
  )
  return (
    <div className="flex flex-col gap-2">
      <AdvancedDataTable<BacktestTrade>
        title="Trades"
        searchPlaceholder="Search a turbo, an exit…"
        emptyTitle="No trade"
        emptyDescription="No signal of entry was taken, or none found a turbo to buy."
        data={trades}
        columns={columns}
        getRowId={(row) => String(row.id)}
        defaultPageSize={10}
        selectable={false}
      />
      <div className="flex justify-end">
        <ExportButton name={`wata-backtest-${runId}-trades`} rows={trades} columns={TRADE_CSV} />
      </div>
    </div>
  )
}

// ── signals ──

const SIGNAL_CSV: CsvColumn<BacktestSignal>[] = [
  { header: 'signal', value: (row) => row.id },
  { header: 'day', value: (row) => row.day },
  { header: 'source', value: (row) => row.source },
  { header: 'action', value: (row) => row.action },
  { header: 'confidence', value: (row) => row.confidence },
  { header: 'bar_time', value: (row) => row.bar_time },
  { header: 'bar_closed_at', value: (row) => row.bar_closed_at },
  { header: 'alert', value: (row) => row.alert?.title },
  { header: 'received_at', value: (row) => row.received_at },
  { header: 'arrived_at', value: (row) => row.arrived_at },
  { header: 'decided_at', value: (row) => row.decided_at },
  { header: 'fate', value: (row) => row.fate },
  { header: 'rule', value: (row) => row.rule },
  { header: 'reason', value: (row) => row.reason },
  { header: 'trade', value: (row) => row.trade_id },
  { header: 'closed_trades', value: (row) => row.closed_trade_ids.join(' ') },
  { header: 'turbos_looked_at', value: (row) => row.candidates?.looked_at },
  { header: 'turbos_in_range', value: (row) => row.candidates?.in_range },
  { header: 'live_outcome', value: (row) => row.live_outcome },
  { header: 'live_reason', value: (row) => row.live_reason },
]

export function SignalsTable({
  runId,
  signals,
  timezone,
  withDay = true,
  onTrade,
}: {
  runId: string
  signals: BacktestSignal[]
  timezone: string
  withDay?: boolean
  onTrade?: (tradeId: number) => void
}) {
  const journal = signals.some((signal) => signal.source === 'journal')
  const columns = useMemo<ColumnDef<BacktestSignal>[]>(
    () => [
      { id: 'signal', header: 'Signal', accessorFn: (row) => row.id, sortable: true, cell: ({ row }) => `#${row.id}` },
      ...(withDay
        ? [{ id: 'day', header: 'Day', accessorKey: 'day' as const, sortable: true, cell: ({ row }: { row: BacktestSignal }) => formatDay(row.day) }]
        : []),
      {
        id: 'when',
        header: 'When',
        accessorFn: (row) => signalTime(row) ?? '',
        sortable: true,
        cell: ({ row }) => (
          <div className="tabular-nums">
            <p>{formatTime(signalTime(row), timezone)}</p>
            <p className="text-xs text-muted-foreground">
              {SOURCE_LABEL[row.source]}
              {row.alert?.title ? ` · ${row.alert.title}` : ''}
            </p>
          </div>
        ),
      },
      {
        id: 'action',
        header: 'Action',
        accessorFn: (row) => row.action ?? '',
        sortable: true,
        cell: ({ row }) => (row.action ? <ActionBadge action={row.action} /> : '-'),
      },
      {
        id: 'fate',
        header: 'What became of it',
        accessorFn: (row) => FATE_LABEL[row.fate],
        sortable: true,
        filterable: true,
        filterOptions: Object.values(FATE_LABEL).map((label) => ({ value: label, label })),
        cell: ({ row }) => (
          <div className="min-w-0 max-w-80 whitespace-normal">
            <FateBadge fate={row.fate} />
            {row.fate !== 'executed' && row.reason && (
              <p className="mt-1 text-xs text-muted-foreground" title={row.reason}>
                {row.rule ? ruleLabel(row.rule) : row.reason}
              </p>
            )}
            {row.candidates && (
              <p className="mt-1 text-xs text-muted-foreground">
                {row.candidates.quoted} turbo{row.candidates.quoted === 1 ? '' : 's'} quoted, {row.candidates.in_range} in range
              </p>
            )}
          </div>
        ),
      },
      {
        id: 'trade',
        header: 'Trade',
        accessorFn: (row) => row.trade_id ?? -1,
        cell: ({ row }) => {
          const ids = [...(row.trade_id !== null ? [row.trade_id] : []), ...row.closed_trade_ids]
          if (ids.length === 0) return '-'
          return (
            <span className="flex flex-wrap gap-1 text-xs">
              {row.trade_id !== null && (
                <Link
                  className="underline underline-offset-2"
                  to={tradePath(runId, row.day, row.trade_id)}
                  onClick={(event) => {
                    if (!onTrade || !isPlainClick(event)) return
                    event.preventDefault()
                    onTrade(row.trade_id as number)
                  }}
                >
                  bought #{row.trade_id}
                </Link>
              )}
              {row.closed_trade_ids.length > 0 && <span>sold {row.closed_trade_ids.map((id) => `#${id}`).join(', ')}</span>}
            </span>
          )
        },
      },
      ...(journal
        ? [
            {
              id: 'live',
              header: 'Live',
              accessorFn: (row: BacktestSignal) => row.live_outcome ?? '',
              sortable: true,
              cell: ({ row }: { row: BacktestSignal }) =>
                row.live_outcome ? (
                  <span className="block max-w-48 whitespace-normal text-xs" title={row.live_reason ?? undefined}>
                    {row.live_outcome}
                  </span>
                ) : (
                  '-'
                ),
            },
          ]
        : []),
    ],
    [runId, timezone, withDay, journal, onTrade],
  )
  return (
    <div className="flex flex-col gap-2">
      <AdvancedDataTable<BacktestSignal>
        title="Signals"
        searchPlaceholder="Search an action, a reason…"
        emptyTitle="No signal"
        data={signals}
        columns={columns}
        getRowId={(row) => String(row.id)}
        defaultPageSize={10}
        selectable={false}
      />
      <div className="flex justify-end">
        <ExportButton name={`wata-backtest-${runId}-signals`} rows={signals} columns={SIGNAL_CSV} />
      </div>
    </div>
  )
}

