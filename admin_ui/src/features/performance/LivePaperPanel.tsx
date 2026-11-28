import { useQuery } from '@tanstack/react-query'
import { formatDay, Panel, QueryState, SignedValue, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from 'ioiti-ui'
import { Link } from 'react-router'

import { paperReportHref, reportLine } from '@/features/backtests/paperModel'
import { OlderCodeBadge } from '@/features/backtests/parts'
import { api } from '@/lib/api'
import type { LivePaperDay } from '@/lib/types'

import { paperState, referenceText } from './livePaper'

function Result({ value, word }: { value: number | null; word?: string | null }) {
  if (word) return <span className="text-muted-foreground">{word}</span>
  if (value === null) return <span className="text-muted-foreground">—</span>
  return <SignedValue value={value} kind="percent" />
}

/**
 * The signals WATA received, traded on paper each finished day under the live settings, beside the real day:
 * while entries are paused, the days the trader would have had. Edge Watch judges them trade by trade.
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
            <p className="text-xs text-muted-foreground">{referenceText(live.reference)}</p>
            {live.report && (
              <p className="text-xs">
                Their report:{' '}
                <Link className="font-medium underline-offset-2 hover:underline" to={paperReportHref('live')}>
                  {reportLine(live.report)}
                </Link>
                , every trade on paper beside WATA’s real one.
              </p>
            )}
            {live.days.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">No paper day yet: the first come once the market is closed.</p>
            ) : (
              <Table density="compact" className="min-w-[30rem]">
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Day</TableHeaderCell>
                    <TableHeaderCell numeric>Real</TableHeaderCell>
                    <TableHeaderCell numeric>On paper</TableHeaderCell>
                    <TableHeaderCell numeric>Trades on paper</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {live.days.map((day: LivePaperDay) => (
                    <TableRow key={day.day}>
                      <TableCell className="font-medium">
                        <span className="inline-flex items-center gap-2">
                          {day.paper_run_id ? (
                            <Link className="underline-offset-2 hover:underline" to={`/backtests/${day.paper_run_id}`}>
                              {formatDay(day.day)}
                            </Link>
                          ) : (
                            formatDay(day.day)
                          )}
                          {/* The signals received are traded on paper whatever the rules: made again. */}
                          {day.paper_stale && <OlderCodeBadge madeWith={day.paper_made_with} madeAgain />}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        <Result value={day.real_percent} />
                      </TableCell>
                      <TableCell className="text-right">
                        <Result value={day.paper_percent} word={paperState(day)} />
                      </TableCell>
                      <TableCell numeric>{day.paper_trades ?? '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
