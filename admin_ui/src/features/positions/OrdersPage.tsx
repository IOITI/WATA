import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  ALL_TIME,
  cn,
  DateRangeFilter,
  ExportButton,
  formatNumber,
  formatTime,
  formatWait,
  KpiTile,
  lastDays,
  PageHeader,
  QueryState,
  rangeParams,
} from 'ioiti-ui'
import type { DateRange } from 'ioiti-ui'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'

import { ORDER_CSV, orderColumns } from './columns'
import { OrderTimeline } from './OrderTimeline'
import { entryFigures } from './orderLife'
import type { EntryFigures } from './orderLife'
import { answerText, orderRows, refusalBefore, refusalFigures, rowId } from './orderRequests'
import type { OrderRow, RefusalFigures } from './orderRequests'
import { RefusalDetail } from './RefusalDetail'

/** How many orders Saxo did not take in the period, and its latest answer. */
function RefusedTile({ refused, timeZone, className }: { refused: RefusalFigures; timeZone: string; className?: string }) {
  const { latest } = refused
  const runs = `${formatNumber(refused.runs)} run${refused.runs === 1 ? '' : 's'} of attempts`
  return (
    <KpiTile
      label="Refused by Saxo"
      value={<span className={cn(refused.attempts > 0 && 'text-negative')}>{formatNumber(refused.attempts)}</span>}
      hint={
        latest ? (
          <span className="line-clamp-2" title={answerText(latest)}>
            {runs}. Last at {formatTime(latest.last_at, timeZone)}: {answerText(latest)}
          </span>
        ) : (
          'Orders Saxo did not take, attempt by attempt'
        )
      }
      className={className}
    />
  )
}

/** How the entries of the period went: is Saxo slow to turn an order into a position, and did any give none? */
function EntryTiles({ figures, refused, timeZone }: { figures: EntryFigures; refused: RefusalFigures; timeZone: string }) {
  if (figures.tracked === 0) {
    return (
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground">
          No order of this period has its moments: they are kept from the first order placed after the version
          that shows this page was installed.
        </p>
        {refused.attempts > 0 && <RefusedTile refused={refused} timeZone={timeZone} className="max-w-sm" />}
      </div>
    )
  }
  const entries = `${formatNumber(figures.tracked)} entr${figures.tracked === 1 ? 'y' : 'ies'} in this period`
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      <KpiTile
        label="Order to position"
        value={figures.typicalMs == null ? '—' : formatWait(figures.typicalMs)}
        hint={`Typical: half were seen sooner. ${entries}`}
      />
      <KpiTile
        label="Slowest entry"
        value={figures.slowestMs == null ? '—' : formatWait(figures.slowestMs)}
        hint={figures.slowestOrderId ? `Order ${figures.slowestOrderId}` : 'No entry gave a position'}
      />
      <KpiTile
        label="Without a position"
        value={
          <span className={cn(figures.withoutPosition > 0 && 'text-negative')}>
            {formatNumber(figures.withoutPosition)}
          </span>
        }
        hint="Accepted by Saxo, then given up or stopped"
      />
      <RefusedTile refused={refused} timeZone={timeZone} />
    </div>
  )
}

function tableTitle(orders: number, runs: number): string {
  const ordersText = `${formatNumber(orders)} order${orders === 1 ? '' : 's'}`
  return runs === 0 ? ordersText : `${ordersText} and ${formatNumber(runs)} refused`
}

export function OrdersPage() {
  const { timezone } = useSession()
  const [searchParams] = useSearchParams()
  // A link from a position: its orders, whenever they were placed.
  const find = searchParams.get('find') ?? ''
  const [range, setRange] = useState<DateRange>(() => (find ? ALL_TIME : lastDays(30)))
  const params = rangeParams(range)

  const ordersQuery = useQuery({
    queryKey: ['orders', params.from, params.to],
    queryFn: () => api.orders(params),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  })

  const columns = useMemo(() => orderColumns(timezone), [timezone])
  const page = ordersQuery.data
  const refusals = useMemo(() => page?.refusals ?? [], [page])
  const rows = useMemo(() => orderRows(page?.items ?? [], refusals), [page, refusals])
  const figures = useMemo(() => entryFigures(page?.items ?? []), [page])
  const refused = useMemo(() => refusalFigures(refusals), [refusals])

  return (
    <>
      <PageHeader
        title="Orders"
        description={`Orders sent to Saxo by the trader and the position monitor, what became of each, and the attempts Saxo did not take. Times are in ${timezone}.`}
      />

      <div className="flex flex-wrap items-center justify-end gap-2">
        <DateRangeFilter value={range} onChange={setRange} />
        <ExportButton name="wata-orders" rows={rows} columns={ORDER_CSV} />
      </div>

      <QueryState isPending={ordersQuery.isPending} error={ordersQuery.error} hasData={!!page}>
        {page && (
          <div className={cn('space-y-4 transition-opacity', ordersQuery.isPlaceholderData && 'opacity-60')}>
            <EntryTiles figures={figures} refused={refused} timeZone={timezone} />
            <div className="space-y-2">
              <AdvancedDataTable<OrderRow>
                // The search starts over when a link brings another order.
                key={find}
                title={tableTitle(page.total, refusals.length)}
                emptyTitle="No order in this range"
                searchPlaceholder="Search instrument, id, Saxo’s answer…"
                data={rows}
                columns={columns}
                getRowId={rowId}
                defaultPageSize={20}
                selectable={false}
                initialSearch={find}
                initialExpandedRowIds={find ? [find] : undefined}
                renderSubComponent={(row) =>
                  row.kind === 'order' ? (
                    <OrderTimeline order={row} refusal={refusalBefore(row, refusals)} timeZone={timezone} />
                  ) : (
                    <RefusalDetail run={row} timeZone={timezone} />
                  )
                }
              />
              <p className="text-xs text-muted-foreground">
                “Took” runs from the order leaving to WATA seeing its position open (or closed), on the server’s clock:
                it holds Saxo’s time and WATA’s own waits between two looks. Open a row for each moment, Saxo’s time
                for the fill among them. A row “Refused by Saxo” is the attempts Saxo did not take for one position
                (or one signal): open it for each attempt and Saxo’s answer.
              </p>
              {page.total > page.items.length && (
                <p className="text-xs text-muted-foreground">
                  Showing the {page.items.length} most recent of {page.total}. Narrow the date range to see older ones.
                </p>
              )}
            </div>
          </div>
        )}
      </QueryState>
    </>
  )
}
