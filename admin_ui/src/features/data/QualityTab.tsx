import { keepPreviousData, skipToken, useQuery } from '@tanstack/react-query'

import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Coverage } from '@/features/backtests/PricesPanel'
import { availabilityOf, instrumentFacts } from '@/features/prices/availability'
import { daysBetween } from '@/features/prices/chartData'
import { GapsPanel } from '@/features/prices/GapsPanel'
import { api } from '@/lib/api'
import { formatDay, formatNumber, formatPercent, formatTime } from '@/lib/format'
import type { AssetDayStatistics, AssetLimits, DataAsset } from '@/lib/types'

import { Facts } from './Facts'

interface QualityTabProps {
  asset: DataAsset
  from: string
  to: string
  limits: AssetLimits
  timeZone: string
  /** The downloads are switched on: what was fetched of each day can be read. */
  downloads: boolean
}

/** Day by day, how much of the trading time has a bar: read from the statistics of the period. */
function CoverageByDay({ asset, from, to, limits, timeZone }: Omit<QualityTabProps, 'downloads'>) {
  const tooLong = daysBetween(from, to) > limits.statistics
  const query = useQuery({
    queryKey: ['data-statistics', asset.ref, from, to],
    queryFn: tooLong ? skipToken : () => api.assetStatistics(asset.ref, { from, to }),
    placeholderData: keepPreviousData,
  })
  const days: AssetDayStatistics[] = query.data?.days ?? []
  return (
    <Panel
      title="Trading time covered, day by day"
      description="The share of each day's trading time with a bar of ten seconds. A day the market is closed has no trading time."
    >
      {tooLong ? (
        <p className="text-sm text-muted-foreground">
          Worked out for {limits.statistics} days at most: choose a shorter period above.
        </p>
      ) : (
        <QueryState isPending={query.isPending} error={query.error} hasData={!!query.data}>
          {days.length === 0 ? (
            <p className="text-sm text-muted-foreground">No bar of this asset in the period.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[32rem] text-left text-sm tabular-nums">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1.5 pr-4 font-medium">Day</th>
                    <th className="py-1.5 pr-4 text-right font-medium">Covered</th>
                    <th className="py-1.5 pr-4 text-right font-medium">Bars</th>
                    <th className="py-1.5 pr-4 text-right font-medium">Without an ask</th>
                    <th className="py-1.5 pr-4 font-medium">First bar</th>
                    <th className="py-1.5 font-medium">Last bar</th>
                  </tr>
                </thead>
                <tbody>
                  {days.map((day) => (
                    <tr key={day.day} className="border-t border-border/50">
                      <td className="py-1.5 pr-4">{formatDay(day.day)}</td>
                      <td className="py-1.5 pr-4 text-right">
                        {day.covered === null ? '—' : formatPercent(Math.floor(day.covered * 1000) / 10, false, 1)}
                      </td>
                      <td className="py-1.5 pr-4 text-right">{formatNumber(day.bars)}</td>
                      <td className="py-1.5 pr-4 text-right">{formatNumber(day.without_ask)}</td>
                      <td className="py-1.5 pr-4">{formatTime(day.first, timeZone)}</td>
                      <td className="py-1.5">{formatTime(day.last, timeZone)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      )}
    </Panel>
  )
}

/** What the recorder kept of a Saxo instrument, and what Saxo left without a quote. */
function RecordedQuality({ asset, from, to, limits, timeZone }: Omit<QualityTabProps, 'downloads'>) {
  const days = daysBetween(from, to)
  const availabilityQuery = useQuery({
    queryKey: ['prices', 'availability', from, to],
    queryFn: () => api.priceAvailability({ from, to }),
    placeholderData: keepPreviousData,
  })
  const contextQuery = useQuery({
    queryKey: ['asset-context', asset.ref, from, to],
    queryFn: () => api.assetContext(asset.ref, { from, to }),
    placeholderData: keepPreviousData,
  })
  const availability = availabilityQuery.data
  const own = availabilityOf(availability, asset)
  const facts = instrumentFacts({ ...asset, volume: contextQuery.data?.volume ?? null }, availability, timeZone)

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title="What was recorded"
        description={
          days > limits.availability
            ? `Worked out from every bar of the period: for ${limits.availability} days at most.`
            : 'From every bar of the period, in trading hours and outside the recorder’s own gaps.'
        }
      >
        <QueryState isPending={availabilityQuery.isPending} error={availabilityQuery.error} hasData={!!availability}>
          <Facts facts={facts} />
        </QueryState>
      </Panel>
      <GapsPanel gaps={contextQuery.data?.gaps} own={own} error={contextQuery.error} timeZone={timeZone} />
      <CoverageByDay asset={asset} from={from} to={to} limits={limits} timeZone={timeZone} />
    </div>
  )
}

/** Which days and hours were fetched from Dukascopy. */
function HistoryQuality({ asset, from, to, limits, timeZone, downloads }: QualityTabProps) {
  const coverageQuery = useQuery({
    queryKey: ['backtest-prices', asset.key, from, to],
    queryFn: downloads ? () => api.priceCoverage({ from, to }, asset.key) : skipToken,
  })
  return (
    <div className="flex flex-col gap-6">
      <Panel
        title="Days fetched"
        description="Dukascopy's files are by day of UTC. A day is written only once every one of its hours was fetched."
      >
        {downloads ? (
          <QueryState isPending={coverageQuery.isPending} error={coverageQuery.error} hasData={!!coverageQuery.data}>
            {coverageQuery.data && <Coverage days={coverageQuery.data.days} />}
          </QueryState>
        ) : (
          <p className="text-sm text-muted-foreground">
            Downloads are switched off in the configuration (<code>admin.backtests_enabled</code>): what was fetched
            of each hour cannot be read here.
          </p>
        )}
      </Panel>
      <CoverageByDay asset={asset} from={from} to={to} limits={limits} timeZone={timeZone} />
    </div>
  )
}

/**
 * Whether the prices of the period can be trusted: what is missing from them,
 * and why when it is known (the recorder down, Saxo silent, hours not fetched).
 */
export function QualityTab(props: QualityTabProps) {
  return props.asset.provider === 'dukascopy' ? <HistoryQuality {...props} /> : <RecordedQuality {...props} />
}
