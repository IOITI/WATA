import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { PageHeader, Panel } from '@/components/Panel'
import { ActionBadge, QueryState } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { api } from '@/lib/api'
import { formatMoney, formatRelative } from '@/lib/format'
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
              <div className="overflow-x-auto">
                <table className="w-full min-w-[44rem] text-left text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="py-2 pr-4 font-medium">Indice</th>
                      <th className="py-2 pr-4 font-medium">Direction</th>
                      <th className="py-2 pr-4 font-medium">Turbo</th>
                      <th className="py-2 pr-4 text-right font-medium">Bid</th>
                      <th className="py-2 pr-4 text-right font-medium">Ask</th>
                      <th className="py-2 pr-4 font-medium">Price seen</th>
                      <th className="py-2 pr-4 font-medium">Search refreshed</th>
                      <th className="py-2 font-medium">State</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {watchlist.items.map((item) => (
                      <tr key={`${item.indice}-${item.direction}`}>
                        <td className="py-2.5 pr-4 font-medium">{item.indice}</td>
                        <td className="py-2.5 pr-4">
                          <ActionBadge action={item.direction} />
                        </td>
                        <td className="py-2.5 pr-4">
                          {item.available ? (
                            <>
                              <p className="max-w-[22rem] truncate font-medium">{item.description ?? '—'}</p>
                              {item.symbol && <p className="text-xs text-muted-foreground">{item.symbol}</p>}
                            </>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </td>
                        <td className="py-2.5 pr-4 text-right tabular-nums">
                          {formatMoney(item.latest_bid, item.currency ?? 'EUR')}
                        </td>
                        <td className="py-2.5 pr-4 text-right tabular-nums">
                          {formatMoney(item.latest_ask, item.currency ?? 'EUR')}
                        </td>
                        <td className="py-2.5 pr-4">{formatRelative(item.price_updated_at, now)}</td>
                        <td className="py-2.5 pr-4">{formatRelative(item.search_refreshed_at, now)}</td>
                        <td className="py-2.5">
                          <StateBadge item={item} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        )}
      </QueryState>
    </>
  )
}
