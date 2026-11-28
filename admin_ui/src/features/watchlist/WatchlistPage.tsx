import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, Badge, formatMoney, formatRelative, PageHeader, Panel, QueryState, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from 'ioiti-ui'
import { useEffect, useState } from 'react'

import { ActionBadge } from '@/components/status'
import { api } from '@/lib/api'
import type { WatchlistItem } from '@/lib/types'

function StateBadge({ item }: { item: WatchlistItem }) {
  if (!item.available) {
    return (
      <Badge tone="warning" dot>
        Nothing cached
      </Badge>
    )
  }
  if (item.stale) {
    return (
      <Badge tone="warning" dot>
        Stale price
      </Badge>
    )
  }
  return (
    <Badge tone="success" dot>
      Ready
    </Badge>
  )
}

export function WatchlistPage() {
  const watchlistQuery = useQuery({ queryKey: ['watchlist'], queryFn: api.watchlist, refetchInterval: 10_000 })
  const watchlist = watchlistQuery.data
  // Re-rendered on a timer so "3 s ago" keeps counting between two refreshes.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [])

  return (
    <>
      <PageHeader
        title="Watchlist"
        description="The turbo the trader would buy right now for each allowed indice and direction, kept ready by the watchlist manager."
      />
      <QueryState isPending={watchlistQuery.isPending} error={watchlistQuery.error} hasData={!!watchlist}>
        {watchlist && !watchlist.enabled && (
          <Alert tone="neutral">
            <AlertDescription>
              The watchlist manager is disabled in the configuration: the trader searches for a turbo when a signal
              arrives.
            </AlertDescription>
          </Alert>
        )}
        {watchlist?.error && (
          <Alert tone="danger" role="alert">
            <AlertDescription>The watchlist manager did not answer: {watchlist.error}</AlertDescription>
          </Alert>
        )}
        {watchlist?.enabled && !watchlist.error && (
          <Panel bodyClassName="pt-2">
            {watchlist.items.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No indice is allowed in the configuration (rule <code>allowed_indices</code>).
              </p>
            ) : (
              <Table density="compact" className="min-w-[44rem]">
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Indice</TableHeaderCell>
                    <TableHeaderCell>Direction</TableHeaderCell>
                    <TableHeaderCell>Turbo</TableHeaderCell>
                    <TableHeaderCell numeric>Bid</TableHeaderCell>
                    <TableHeaderCell numeric>Ask</TableHeaderCell>
                    <TableHeaderCell>Price seen</TableHeaderCell>
                    <TableHeaderCell>Search refreshed</TableHeaderCell>
                    <TableHeaderCell>State</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {watchlist.items.map((item) => (
                    <TableRow key={`${item.indice}-${item.direction}`}>
                      <TableCell className="font-medium">{item.indice}</TableCell>
                      <TableCell>
                        <ActionBadge action={item.direction} />
                      </TableCell>
                      <TableCell>
                        {item.available ? (
                          <>
                            <p className="max-w-[22rem] truncate font-medium">{item.description ?? '—'}</p>
                            {item.symbol && <p className="text-xs text-muted-foreground">{item.symbol}</p>}
                          </>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell numeric>
                        {formatMoney(item.latest_bid, item.currency ?? 'EUR')}
                      </TableCell>
                      <TableCell numeric>
                        {formatMoney(item.latest_ask, item.currency ?? 'EUR')}
                      </TableCell>
                      <TableCell>{formatRelative(item.price_updated_at, now)}</TableCell>
                      <TableCell>{formatRelative(item.search_refreshed_at, now)}</TableCell>
                      <TableCell>
                        <StateBadge item={item} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Panel>
        )}
      </QueryState>
    </>
  )
}
