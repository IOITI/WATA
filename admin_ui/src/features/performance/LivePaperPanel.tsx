import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { Panel } from '@/components/Panel'
import { PnlValue, QueryState } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { api } from '@/lib/api'
import { formatDay } from '@/lib/format'
import type { LivePaperDay } from '@/lib/types'

import { paperState, referenceText } from './livePaper'

function Result({ value, outside, word }: { value: number | null; outside: boolean | null; word?: string | null }) {
  if (word) return <span className="text-muted-foreground">{word}</span>
  if (value === null) return <span className="text-muted-foreground">—</span>
  return (
    <span className="inline-flex items-center justify-end gap-2">
      <PnlValue value={value} kind="percent" />
      {outside && <Badge tone="warning">Outside</Badge>}
    </span>
  )
}

/**
 * The signals WATA received, traded on paper each finished day under the live settings, beside the real day
 * and the live reference's daily range: while entries are paused, the days the trader would have had.
 */
export function LivePaperPanel({ enabled }: { enabled: boolean }) {
  const query = useQuery({ queryKey: ['live-paper'], queryFn: api.livePaper, enabled, staleTime: 60_000 })
  const live = query.data
  if (!enabled) return null

  return (
    <Panel
      title="Paper days of the signals received"
      description="Each finished weekday, the signals WATA received replayed under the live settings, beside what WATA really made."
      bodyClassName="pt-2"
    >
      <QueryState isPending={query.isPending} error={query.error} hasData={!!live}>
        {live && (
          <div className="space-y-3">
            {live.warning && (
              <Alert tone="warning">
                <AlertTitle>The live settings left their reference’s range</AlertTitle>
                <AlertDescription>{live.warning}.</AlertDescription>
              </Alert>
            )}
            <p className="text-xs text-muted-foreground">{referenceText(live.reference)}</p>
            {live.days.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">No paper day yet: the first come once the market is closed.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[30rem] text-left text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="py-2 pr-4 font-medium">Day</th>
                      <th className="py-2 pr-4 text-right font-medium">Real</th>
                      <th className="py-2 pr-4 text-right font-medium">On paper</th>
                      <th className="py-2 text-right font-medium">Trades on paper</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {live.days.map((day: LivePaperDay) => (
                      <tr key={day.day}>
                        <td className="py-2.5 pr-4 font-medium">
                          {day.paper_run_id ? (
                            <Link className="underline-offset-2 hover:underline" to={`/backtests/${day.paper_run_id}`}>
                              {formatDay(day.day)}
                            </Link>
                          ) : (
                            formatDay(day.day)
                          )}
                        </td>
                        <td className="py-2.5 pr-4 text-right">
                          <Result value={day.real_percent} outside={day.real_outside} />
                        </td>
                        <td className="py-2.5 pr-4 text-right">
                          <Result value={day.paper_percent} outside={day.paper_outside} word={paperState(day)} />
                        </td>
                        <td className="py-2.5 text-right tabular-nums">{day.paper_trades ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
