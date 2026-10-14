import { keepPreviousData, skipToken, useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'

import { PageHeader, Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { AnimatedTabs } from '@/components/ui/animated-tabs'
import { Button } from '@/components/ui/button'
import type { DateRange } from '@/components/ui/calendar'
import { DateRangePicker } from '@/components/ui/date-picker'
import { useSession } from '@/features/auth/auth-context'
import { Coverage } from '@/features/backtests/PricesPanel'
import { availabilityOf } from '@/features/prices/availability'
import { daysBetween, instrumentLabel, todayIn } from '@/features/prices/chartData'
import { parseView, periodPresets, viewParams, withPeriod } from '@/features/prices/view'
import type { ChartView } from '@/features/prices/view'
import { api, ApiError } from '@/lib/api'
import { formatDay, toIsoDay } from '@/lib/format'
import type { AssetDetails } from '@/lib/types'

import { AssetChart } from './AssetChart'
import { ASSETS_PATH, PROVIDER_LABELS } from './assets'
import { DetailsTab } from './DetailsTab'
import { QualityTab } from './QualityTab'
import { StatisticsTab } from './StatisticsTab'
import { TradesTab } from './TradesTab'

const TABS = [
  { id: 'chart', label: 'Chart' },
  { id: 'statistics', label: 'Statistics' },
  { id: 'quality', label: 'Data quality' },
  { id: 'trades', label: 'Trades' },
  { id: 'details', label: 'Details' },
]
type Tab = 'chart' | 'statistics' | 'quality' | 'trades' | 'details'

function parseTab(value: string | null): Tab {
  return TABS.some((tab) => tab.id === value) ? (value as Tab) : 'chart'
}

function dayToDate(day: string): Date {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date)
}

interface PeriodFilterProps {
  view: ChartView
  /** The day the presets count back from: today for an asset still quoted, else its last day with data. */
  anchor: string
  today: string
  onChange: (from: string, to: string) => void
}

function PeriodFilter({ view, anchor, today, onChange }: PeriodFilterProps) {
  // A range of which only the first day is picked yet: not a period to ask the server for.
  const [picking, setPicking] = useState<DateRange | null>(null)

  return (
    <div className="flex flex-wrap items-center gap-2">
      {periodPresets(anchor, today).map((preset) => {
        const active = preset.from === view.from && preset.to === view.to
        return (
          <Button
            key={preset.label}
            variant={active ? 'primary' : 'outline'}
            size="sm"
            aria-pressed={active}
            onClick={() => {
              setPicking(null)
              onChange(preset.from, preset.to)
            }}
          >
            {preset.label}
          </Button>
        )
      })}
      <DateRangePicker
        value={picking ?? { from: dayToDate(view.from), to: dayToDate(view.to) }}
        onChange={(range) => {
          if (range.from && range.to) {
            setPicking(null)
            onChange(toIsoDay(range.from), toIsoDay(range.to))
          } else {
            setPicking(range)
          }
        }}
        placeholder="Custom range"
        align="end"
        locale="en-GB"
        weekStartsOn={1}
        maxDate={dayToDate(today)}
      />
    </div>
  )
}

function NotFound({ refused }: { refused: string }) {
  return (
    <>
      <PageHeader title="No such asset" />
      <Panel title="This asset is not stored">
        <p className="text-sm text-muted-foreground">
          {refused} Its prices may have been dropped by the recorder&rsquo;s retention, or the address is not one of an
          asset.{' '}
          <Link to={ASSETS_PATH} className="text-foreground underline underline-offset-2">
            Every asset stored
          </Link>{' '}
          is on the Assets View.
        </p>
      </Panel>
    </>
  )
}

/**
 * One asset: its chart with all the ways to read it, the statistics of each
 * day and of the spread, what is missing from its data, WATA's trades on it,
 * and everything stored about it. The period, the bars and the tab are in
 * the address.
 */
export function AssetPage() {
  const { provider = '', key = '' } = useParams()
  const ref = `${provider}/${key}`
  const detailsQuery = useQuery({ queryKey: ['data-asset', ref], queryFn: () => api.asset(ref) })

  if (detailsQuery.error instanceof ApiError && detailsQuery.error.status === 404) {
    return <NotFound refused={detailsQuery.error.message.endsWith('.') ? detailsQuery.error.message : `${detailsQuery.error.message}.`} />
  }
  return (
    <QueryState isPending={detailsQuery.isPending} error={detailsQuery.error} hasData={!!detailsQuery.data}>
      {detailsQuery.data && <AssetView details={detailsQuery.data} />}
    </QueryState>
  )
}

function AssetView({ details }: { details: AssetDetails }) {
  const { timezone, backtests_enabled: downloads } = useSession()
  const { asset } = details
  const [searchParams, setSearchParams] = useSearchParams()
  const today = todayIn(timezone)
  // An asset opens on its last day with data, which is today for one quoted today.
  const anchor = details.last_day
  const view = useMemo(() => parseView(searchParams, anchor), [searchParams, anchor])
  const tab = parseTab(searchParams.get('tab'))
  const name = instrumentLabel(asset)
  const recorded = asset.provider === 'saxo'

  // Going through bars and periods must not leave as many steps to go back through.
  const show = useCallback(
    (next: ChartView, nextTab: Tab = tab) => {
      const params = viewParams(next, anchor)
      if (nextTab !== 'chart') params.set('tab', nextTab)
      setSearchParams(params, { replace: true })
    },
    [anchor, setSearchParams, tab],
  )

  // Live while the recorder still names it and the period reaches today.
  const live = recorded && asset.status === 'recording' && view.to >= today
  // Under a turbo, the index recorded in the period: the one of its last day comes with the asset.
  const otherPeriod = view.from !== details.last_day || view.to !== details.last_day
  const indexQuery = useQuery({
    queryKey: ['data-asset', asset.ref, view.from, view.to],
    queryFn:
      recorded && asset.role === 'turbo' && otherPeriod ? () => api.asset(asset.ref, { from: view.from, to: view.to }) : skipToken,
    placeholderData: keepPreviousData,
  })
  const index = otherPeriod ? (indexQuery.data?.index ?? details.index) : details.index
  // What Saxo left without a quote, beside the recorder's own gaps: worked out for a few days at most.
  const availabilityQuery = useQuery({
    queryKey: ['prices', 'availability', view.from, view.to],
    queryFn:
      recorded && tab === 'chart' && daysBetween(view.from, view.to) <= details.max_days.availability
        ? () => api.priceAvailability({ from: view.from, to: view.to })
        : skipToken,
    placeholderData: keepPreviousData,
  })
  // A Dukascopy history has no recorder behind it: what was fetched of each day stands in for its gaps.
  const coverageQuery = useQuery({
    queryKey: ['backtest-prices', asset.key, view.from, view.to],
    queryFn:
      !recorded && downloads && tab === 'chart' ? () => api.priceCoverage({ from: view.from, to: view.to }, asset.key) : skipToken,
  })
  const tooLong = !recorded && daysBetween(view.from, view.to) > details.max_days.chart

  return (
    <>
      <PageHeader
        title={name}
        description={[PROVIDER_LABELS[asset.provider], asset.description, asset.symbol, asset.currency]
          .filter(Boolean)
          .join(' · ')}
        actions={
          <PeriodFilter
            view={view}
            anchor={anchor}
            today={today}
            onChange={(from, to) => show(withPeriod(view, from, to))}
          />
        }
      />
      <p className="-mt-3 text-xs text-muted-foreground">
        {anchor === today
          ? `Times are in ${timezone}.`
          : `Its last day with data is ${formatDay(anchor)}. Times are in ${timezone}.`}
      </p>

      <AnimatedTabs tabs={TABS} activeTab={tab} onChange={(next) => show(view, parseTab(next))} renderContent={false} />

      {tab === 'chart' &&
        (tooLong ? (
          <Panel title="A shorter period">
            <p className="text-sm text-muted-foreground">
              The Dukascopy history is read {details.max_days.chart} days at a time at most: choose a shorter period above.
            </p>
          </Panel>
        ) : (
          <AssetChart
            asset={asset}
            index={index}
            view={view}
            onViewChange={(next) => show(next)}
            live={live}
            availability={availabilityOf(availabilityQuery.data, asset)}
            coverage={
              recorded ? undefined : (
                <Panel
                  title="Days fetched"
                  description="Dukascopy's files are by day of UTC; a day is written once every one of its hours was fetched."
                >
                  {downloads ? (
                    coverageQuery.data ? (
                      <Coverage days={coverageQuery.data.days} />
                    ) : (
                      <p className="text-sm text-muted-foreground">Reading what was fetched…</p>
                    )
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Downloads are switched off in the configuration (<code>admin.backtests_enabled</code>).
                    </p>
                  )}
                </Panel>
              )
            }
          />
        ))}
      {tab === 'statistics' && (
        <StatisticsTab asset={asset} from={view.from} to={view.to} limits={details.max_days} timeZone={timezone} />
      )}
      {tab === 'quality' && (
        <QualityTab
          asset={asset}
          from={view.from}
          to={view.to}
          limits={details.max_days}
          timeZone={timezone}
          downloads={downloads}
        />
      )}
      {tab === 'trades' && <TradesTab asset={asset} from={view.from} to={view.to} timeZone={timezone} />}
      {tab === 'details' && <DetailsTab details={details} timeZone={timezone} />}
    </>
  )
}
