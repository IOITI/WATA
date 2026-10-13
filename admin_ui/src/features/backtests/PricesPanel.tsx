import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Square } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import type { DateRange } from '@/components/ui/calendar'
import { DateRangePicker } from '@/components/ui/date-picker'
import { SpringSelect } from '@/components/ui/spring-select'
import { StretchSwitch } from '@/components/ui/stretch-switch'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { lastDays, rangeParams } from '@/lib/dateRange'
import { formatDateTime, formatDay } from '@/lib/format'
import type { PriceCoverageDay, PriceFetchStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

import { keepSentence } from './model'

const DAY_STATE: Record<PriceCoverageDay['state'], { label: string; className: string }> = {
  ready: { label: 'ready', className: 'bg-positive' },
  partial: { label: 'ready, its last hours not published yet', className: 'bg-positive/50' },
  incomplete: { label: 'some hours fetched', className: 'bg-warning' },
  missing: { label: 'not fetched', className: 'border border-border' },
  unavailable: { label: 'nothing at the source', className: 'bg-muted-foreground/40' },
  closed: { label: 'market closed', className: '' },
}
// In the order they are explained under the days.
const LEGEND: PriceCoverageDay['state'][] = ['ready', 'partial', 'incomplete', 'missing', 'unavailable']
// How far back the service can be told to keep the prices: a month and a half to two years of trading days.
const KEEP_DAYS = [30, 60, 120, 250, 500]

const FETCH_STATE: Record<PriceFetchStatus['state'], string> = {
  idle: '',
  running: 'Downloading',
  done: 'Downloaded',
  incomplete: 'Ended with days still missing: the server refused part of it. Run the same period again.',
  stopped: 'Stopped. What was fetched is kept.',
  failed: 'The download failed.',
  interrupted: 'Interrupted when the admin restarted. What was fetched is kept.',
}

interface Month {
  label: string
  days: PriceCoverageDay[]
}

/** The days by month, weekends left out: nothing is ever fetched for them. */
function byMonth(days: readonly PriceCoverageDay[]): Month[] {
  const months = new Map<string, PriceCoverageDay[]>()
  for (const day of days) {
    if (day.state === 'closed') continue
    const key = day.day.slice(0, 7)
    months.set(key, [...(months.get(key) ?? []), day])
  }
  return [...months.entries()].map(([key, monthDays]) => ({
    label: new Intl.DateTimeFormat('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' }).format(
      new Date(`${key}-01T00:00:00Z`),
    ),
    days: monthDays,
  }))
}

function Coverage({ days }: { days: readonly PriceCoverageDay[] }) {
  const months = useMemo(() => byMonth(days), [days])
  return (
    <div className="flex flex-col gap-2">
      {months.map((month) => (
        <div key={month.label} className="flex items-center gap-3">
          <span className="w-16 shrink-0 text-xs text-muted-foreground">{month.label}</span>
          <ul className="flex flex-wrap gap-1" aria-label={`Prices of ${month.label}`}>
            {month.days.map((day) => (
              <li
                key={day.day}
                title={`${formatDay(day.day)}: ${DAY_STATE[day.state].label}${
                  day.state === 'incomplete' ? ` (${day.hours} of ${day.hours_expected} hours)` : ''
                }`}
                className={cn('size-3 rounded-[3px]', DAY_STATE[day.state].className)}
              >
                <span className="sr-only">
                  {formatDay(day.day)}: {DAY_STATE[day.state].label}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <ul className="mt-1 flex flex-wrap items-center gap-4 text-xs text-muted-foreground" aria-hidden>
        {/* The three every period has, and the two rarer ones when a day is in one of them. */}
        {LEGEND.filter(
          (state) => ['ready', 'incomplete', 'missing'].includes(state) || days.some((day) => day.state === state),
        ).map((state) => (
          <li key={state} className="flex items-center gap-1.5">
            <span className={cn('size-3 rounded-[3px]', DAY_STATE[state].className)} />
            {DAY_STATE[state].label}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Which days have prices to backtest on, and the download of more. */
export function PricesPanel() {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const [range, setRange] = useState<DateRange>(() => lastDays(30))
  const period = rangeParams(range)

  const coverageQuery = useQuery({
    queryKey: ['backtest-prices'],
    queryFn: () => api.priceCoverage(),
    // While a download runs its messages and the days it completes are worth following.
    refetchInterval: (query) => (query.state.data?.fetch.state === 'running' ? 3_000 : 60_000),
  })
  const coverage = coverageQuery.data
  const fetching = coverage?.fetch.state === 'running'

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['backtest-prices'] })
  const fetchMutation = useMutation({
    mutationFn: () => api.fetchPrices({ from: period.from ?? '', to: period.to ?? '' }),
    onSuccess: refresh,
  })
  const stopMutation = useMutation({ mutationFn: api.stopFetchingPrices, onSuccess: refresh })
  const keepMutation = useMutation({ mutationFn: api.keepPrices, onSuccess: refresh })
  const failure = fetchMutation.error ?? stopMutation.error ?? keepMutation.error
  const keep = coverage?.keep
  const keepDays = keep ? [...new Set([...KEEP_DAYS, keep.days])].sort((a, b) => a - b) : KEEP_DAYS

  return (
    <Panel
      title="Prices"
      description={
        coverage?.ready.first
          ? `${coverage.ready.count} day${coverage.ready.count === 1 ? '' : 's'} of the index ready to backtest on, from ${formatDay(coverage.ready.first)} to ${formatDay(coverage.ready.last)}.`
          : 'No day of prices yet: fetch some before asking for a backtest.'
      }
    >
      <QueryState isPending={coverageQuery.isPending} error={coverageQuery.error} hasData={!!coverage}>
        {coverage && (
          <div className="flex flex-col gap-5">
            <Coverage days={coverage.days} />

            {keep && (
              <div className="flex flex-col gap-3 border-t border-border/60 pt-4">
                <StretchSwitch
                  label="Keep the prices up to date"
                  description={keepSentence(keep, (moment) => formatDateTime(moment, timezone, false))}
                  checked={keep.enabled}
                  disabled={keepMutation.isPending}
                  onChange={(enabled) => keepMutation.mutate({ enabled, days: keep.days })}
                />
                <div className="max-w-56">
                  <SpringSelect
                    label="How far back"
                    options={keepDays.map((days) => ({ value: String(days), label: `${days} days` }))}
                    value={String(keep.days)}
                    onChange={(days) => keepMutation.mutate({ enabled: keep.enabled, days: Number(days) })}
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  The service then fetches what is missing by itself: the newest days first, a few at a time, only while
                  the market is closed, and it waits longer each time the source stops answering. A day of prices takes
                  about 5 MB of disk.
                </p>
              </div>
            )}

            <div className="flex flex-col gap-3 border-t border-border/60 pt-4">
              <div className="flex flex-wrap items-center gap-2">
                <DateRangePicker
                  value={range}
                  onChange={setRange}
                  placeholder="Period to fetch"
                  align="start"
                  locale="en-GB"
                  weekStartsOn={1}
                  disabled={fetching}
                />
                {fetching ? (
                  <Button
                    variant="outline"
                    size="sm"
                    leftIcon={<Square className="size-3.5" />}
                    isLoading={stopMutation.isPending}
                    onClick={() => stopMutation.mutate()}
                  >
                    Stop the download
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    leftIcon={<Download className="size-3.5" />}
                    disabled={!period.from || !period.to}
                    isLoading={fetchMutation.isPending}
                    onClick={() => fetchMutation.mutate()}
                  >
                    Fetch these days
                  </Button>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                From Dukascopy, another broker than the one WATA trades with. Its server refuses many requests: a
                month takes hours, and usually several downloads of the same period. One runs at a time.
              </p>

              {failure && (
                <Alert tone="danger" role="alert">
                  <AlertDescription>{failure.message}</AlertDescription>
                </Alert>
              )}

              {coverage.fetch.state !== 'idle' && (
                <div className="rounded-xl border border-border/60 p-3" aria-live="polite">
                  <p className="text-sm">
                    {coverage.fetch.automatic && coverage.fetch.state === 'incomplete'
                      ? 'Ended with days still missing: the server refused part of it. The service will ask again later.'
                      : FETCH_STATE[coverage.fetch.state]}
                    {coverage.fetch.from && (
                      <span className="text-muted-foreground">
                        {' '}
                        ({formatDay(coverage.fetch.from)} to {formatDay(coverage.fetch.to)})
                      </span>
                    )}
                  </p>
                  {coverage.fetch.lines.length > 0 && (
                    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs text-muted-foreground">
                      {coverage.fetch.lines.join('\n')}
                    </pre>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
