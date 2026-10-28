import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { AdvancedDataTable, cn, formatNumber, KpiTile, QueryState, SignedValue } from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { useMemo } from 'react'
import { Link } from 'react-router'

import { positionColumns } from '@/features/positions/columns'
import { positionLink } from '@/features/positions/orderLife'
import { api } from '@/lib/api'
import type { DataAsset, Position } from '@/lib/types'

interface TradesTabProps {
  asset: DataAsset
  from: string
  to: string
  timeZone: string
}

const LINK: ColumnDef<Position> = {
  id: 'link',
  header: '',
  align: 'right',
  cell: ({ row }) => (
    <Link
      to={positionLink(row.position_id)}
      className="text-xs text-muted-foreground underline decoration-border underline-offset-2 hover:text-foreground"
      aria-label={`Position ${row.position_id} on the Positions page`}
    >
      Open
    </Link>
  ),
}

/**
 * The positions WATA held on the asset in the period: on the turbo itself,
 * or on the turbos of the index that the recorder followed. The same rows as
 * the Positions page, each a link to it.
 */
export function TradesTab({ asset, from, to, timeZone }: TradesTabProps) {
  const query = useQuery({
    queryKey: ['asset-context', asset.ref, from, to],
    queryFn: () => api.assetContext(asset.ref, { from, to }),
    placeholderData: keepPreviousData,
  })
  const trades = useMemo(() => query.data?.trades ?? [], [query.data])
  const columns = useMemo(() => [...positionColumns(timeZone, 'all', { canClose: false }), LINK], [timeZone])
  const closed = trades.filter((trade) => trade.position_status !== 'Open')
  const won = closed.filter((trade) => (trade.position_profit_loss ?? 0) > 0).length
  const profit = closed.reduce((sum, trade) => sum + (trade.position_profit_loss ?? 0), 0)
  const currency = trades.find((trade) => trade.instrument_currency)?.instrument_currency ?? 'EUR'
  const index = asset.role === 'underlying'

  return (
    <QueryState isPending={query.isPending} error={query.error} hasData={!!query.data}>
      <div className={cn('flex flex-col gap-6 transition-opacity', query.isPlaceholderData && 'opacity-60')}>
        <div className="grid gap-4 sm:grid-cols-3">
          <KpiTile
            label="Positions"
            value={formatNumber(trades.length)}
            hint={index ? `On the turbos of ${asset.indice ?? 'this index'} the recorder followed` : 'On this turbo'}
          />
          <KpiTile label="Won" value={closed.length > 0 ? `${won} of ${closed.length}` : '—'} hint="Of those closed" />
          <KpiTile
            label="Profit / loss"
            value={closed.length > 0 ? <SignedValue value={profit} kind="money" currency={currency} /> : '—'}
            hint="Of those closed in the period"
          />
        </div>
        <AdvancedDataTable<Position>
          title="Positions"
          data={trades}
          columns={columns}
          getRowId={(row) => row.position_id}
          selectable={false}
          defaultPageSize={10}
          emptyTitle="No position in this period"
          emptyDescription={
            index
              ? 'A trade on a turbo the recorder never followed is not counted here: the Positions page has every one.'
              : undefined
          }
        />
      </div>
    </QueryState>
  )
}
