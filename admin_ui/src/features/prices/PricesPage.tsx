import { keepPreviousData, skipToken, useQuery, useQueryClient } from '@tanstack/react-query'
import { Maximize2 } from 'lucide-react'
import { useCallback, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { ExportButton } from '@/components/ExportButton'
import { PageHeader, Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { useTheme } from '@/components/theme-context'
import { Button } from '@/components/ui/button'
import type { DateRange } from '@/components/ui/calendar'
import { DateRangePicker } from '@/components/ui/date-picker'
import { Skeleton } from '@/components/ui/skeleton'
import { StretchSwitch } from '@/components/ui/stretch-switch'
import { useSession } from '@/features/auth/auth-context'
import { api, ApiError } from '@/lib/api'
import { formatDay, toIsoDay } from '@/lib/format'
import type { PriceInstrument } from '@/lib/types'
import { cn } from '@/lib/utils'

import { availabilityOf } from './availability'
import { RangeBarsTable, TimeBarsTable } from './BarsTable'
import { ChartAttribution } from './ChartAttribution'
import {
  allowedIntervals,
  daysBetween,
  groupInstruments,
  instrumentLabel,
  intervalLabel,
  INTERVALS,
  signalMarkers,
  ticksOf,
  todayIn,
  tradeMarkers,
} from './chartData'
import type { ChartMarker, Side } from './chartData'
import { rangeBarsCsv, rangeChart, timeBarsCsv, timeChart } from './chartModel'
import type { ChartModel, TimeBars } from './chartModel'
import { GapsPanel } from './GapsPanel'
import { InstrumentPicker } from './InstrumentPicker'
import { PriceChart } from './PriceChart'
import { PriceReadout, RangeReadout } from './PriceReadout'
import { RangeControl } from './RangeControl'
import type { BuiltRange } from './RangeControl'
import { rememberedRange, rememberRange } from './rangeMemory'
import { rememberedSignals, rememberSignals } from './signalMemory'
import { parseView, periodPresets, viewParams, withPeriod } from './view'
import type { PriceView } from './view'

const NO_INSTRUMENTS: PriceInstrument[] = []
const NO_MARKERS: ChartMarker[] = []
/** How often a period that includes today is read again. A bar closes every ten seconds; nobody watches that closely. */
const REFRESH_MS = 30_000
/** What is missing from each instrument is read from every bar of the period: less often. */
const AVAILABILITY_REFRESH_MS = 120_000
/** How long the bars of a past period are kept without asking again: they no longer change. */
const PAST_FRESH_MS = 10 * 60_000
const SIDES: { id: Side; label: string }[] = [
  { id: 'bid', label: 'Bid' },
  { id: 'ask', label: 'Ask' },
]

function dayToDate(day: string): Date {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date)
}

interface PeriodFilterProps {
  view: PriceView
  today: string
  onChange: (from: string, to: string) => void
}

function PeriodFilter({ view, today, onChange }: PeriodFilterProps) {
  // A range of which only the first day is picked yet: not a period to ask the server for.
  const [picking, setPicking] = useState<DateRange | null>(null)

  return (
    <div className="flex flex-wrap items-center gap-2">
      {periodPresets(today).map((preset) => {
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

function NothingRecorded({ view }: { view: PriceView }) {
  const period = view.from === view.to ? `on ${formatDay(view.from)}` : `from ${formatDay(view.from)} to ${formatDay(view.to)}`
  return (
    <Panel title="No price recorded in this period">
      <div className="max-w-2xl space-y-3 text-sm text-muted-foreground">
        <p>
          Nothing was kept {period}: the market was closed, or the price recorder was not running. Choose another
          period above.
        </p>
        <p>
          The recorder is an optional service. To switch it on, set{' '}
          <code className="text-foreground">trade.config.price_recorder.enabled</code> to true in the{' '}
          <Link to="/config" className="text-foreground underline underline-offset-2">
            configuration
          </Link>
          , add <code className="text-foreground">recorder</code> to <code className="text-foreground">COMPOSE_PROFILES</code>{' '}
          on the server and start it. Its state is listed with the services on the{' '}
          <Link to="/system" className="text-foreground underline underline-offset-2">
            System page
          </Link>
          .
        </p>
      </div>
    </Panel>
  )
}

function Legend({ indexName, hasTrades, hasSignals }: { indexName?: string; hasTrades: boolean; hasSignals: boolean }) {
  return (
    <ul aria-label="Legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="h-3 w-1.5 rounded-[1px] border" style={{ borderColor: 'var(--chart-positive)' }} />
        Rose
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="h-3 w-1.5 rounded-[1px]" style={{ backgroundColor: 'var(--chart-negative)' }} />
        Fell
      </li>
      {indexName && (
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="h-0.5 w-3 rounded-full" style={{ backgroundColor: 'var(--chart-series)' }} />
          {indexName}, lower panel
        </li>
      )}
      {hasTrades && (
        <>
          <li>
            <span aria-hidden>▲</span> Bought
          </li>
          <li>
            <span aria-hidden>▼</span> Sold
          </li>
        </>
      )}
      {hasSignals && (
        <li>
          <span aria-hidden>●</span> Signal
        </li>
      )}
    </ul>
  )
}

/**
 * What a range bar is and how these are made: read once, so closed until asked
 * for. A disclosure, not a tooltip: a finger and a keyboard open it too.
 */
function AboutRangeBars({ tick, side, unit }: { tick: string; side: Side; unit: string }) {
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="w-fit cursor-pointer rounded text-foreground/80 hover:text-foreground">About range bars</summary>
      <div className="mt-2 max-w-3xl space-y-1.5">
        <p>
          A range bar ends when the price has left a range of the size set, however long that took. It closes at its
          high or at its low, and the next bar opens there.
        </p>
        <p>
          R counts ticks, as in TradingView: 1R is one tick, the smallest step of these prices ({tick} {unit}).
          TradingView counts in the ticks of its own symbol, which may be another size (0.25 on NQ futures, where
          60R is 15 points): compare the price move, not the number of ticks.
        </p>
        <p>
          Built from the ten-second bars of the {side}. Inside ten seconds the low is taken to come before the high
          when the price rose, after it when it fell.
        </p>
        <p>
          Nothing is made up for a time that was not recorded: after more than a minute without a bar, the bar in
          progress ends as it is and the chart leaves one empty slot.
        </p>
      </div>
    </details>
  )
}

function Export({ chart }: { chart: ChartModel | undefined }) {
  const symbol = chart?.instrument.symbol ?? chart?.instrument.uic ?? 'none'
  const timeCsv = useMemo(() => timeBarsCsv(chart?.timeZone ?? 'UTC'), [chart?.timeZone])
  const rangeCsv = useMemo(() => rangeBarsCsv(chart?.timeZone ?? 'UTC'), [chart?.timeZone])
  if (chart?.kind === 'range') {
    return <ExportButton name={`wata-prices-${symbol}-range-${chart.size}`} rows={chart.rows} columns={rangeCsv} />
  }
  return (
    <ExportButton name={`wata-prices-${symbol}-${chart?.interval ?? 0}s`} rows={chart?.bars ?? []} columns={timeCsv} />
  )
}

export function PricesPage() {
  const { timezone } = useSession()
  const { theme } = useTheme()
  const [searchParams, setSearchParams] = useSearchParams()
  const today = todayIn(timezone)
  const view = useMemo(() => parseView(searchParams, today), [searchParams, today])
  // Going through twenty turbos must not leave twenty steps to go back through.
  const show = useCallback(
    (next: PriceView) => setSearchParams(viewParams(next, today), { replace: true }),
    [setSearchParams, today],
  )

  const period = useMemo(() => ({ from: view.from, to: view.to }), [view.from, view.to])
  const live = view.to >= today
  const refetchInterval = live ? REFRESH_MS : false
  const possibleIntervals = allowedIntervals(daysBetween(view.from, view.to))
  // Range bars (a bar ends when the price has moved enough) or bars of a fixed length.
  const ranged = view.range !== null

  const instrumentsQuery = useQuery({
    queryKey: ['prices', 'instruments', view.from, view.to],
    queryFn: () => api.priceInstruments(period),
    refetchInterval,
    placeholderData: keepPreviousData,
  })
  const instruments = instrumentsQuery.data?.items ?? NO_INSTRUMENTS
  // Apart from the list, which it must not slow down: the marks arrive a moment after the names.
  // Nothing is kept from the period before: its holes are not this one's. The server reads every
  // bar of the period for it, so only today alone is read again while the page stays open.
  const availabilityQuery = useQuery({
    queryKey: ['prices', 'availability', view.from, view.to],
    queryFn: () => api.priceAvailability(period),
    refetchInterval: live && view.from === view.to ? AVAILABILITY_REFRESH_MS : false,
    staleTime: live ? AVAILABILITY_REFRESH_MS / 2 : PAST_FRESH_MS,
  })
  const availability = availabilityQuery.data
  const selected =
    instruments.find((instrument) => instrument.id === view.instrument) ??
    groupInstruments(instruments).index[0] ??
    instruments[0]
  // Under a turbo, the index it follows.
  const index =
    selected?.role === 'turbo'
      ? instruments.find((instrument) => instrument.role === 'underlying' && instrument.indice === selected.indice)
      : undefined

  // The bars of one instrument, shared between charts: the index under one turbo is the index under the next.
  const queryClient = useQueryClient()
  const loadBars = (instrumentId: number) =>
    queryClient.fetchQuery({
      queryKey: ['prices', 'bars', instrumentId, view.from, view.to, view.interval],
      queryFn: () => api.priceBars(instrumentId, period, view.interval),
      staleTime: live ? REFRESH_MS / 2 : PAST_FRESH_MS,
    })
  // The prices and their index are asked for together and arrive together. Given one
  // after the other, the chart would for a moment hold two panels that do not go
  // with each other, and the charting library does not recover from that.
  const timeQuery = useQuery({
    queryKey: ['prices', 'chart', selected?.id, index?.id ?? null, view.from, view.to, view.interval],
    queryFn:
      selected && !ranged
        ? async (): Promise<TimeBars> => {
            const [prices, indexPrices] = await Promise.all([loadBars(selected.id), index ? loadBars(index.id) : null])
            return { prices, index: indexPrices }
          }
        : skipToken,
    refetchInterval,
    placeholderData: keepPreviousData,
  })
  // In Auto the server chooses the range from what the price covered in the period. Asked
  // again at each refresh of a day still going, it would sooner or later choose another one
  // and redraw the whole chart with other bars under the reader's eyes. So the choice is
  // made once for a view (an instrument, a period, a side) and asked for by name afterwards.
  const autoSizes = useRef(new Map<string, number>())
  const autoKey = `${selected?.id}|${view.from}|${view.to}|${view.side}`
  const askedRange = view.range
  // Range bars come with the index at the end of each of them: one answer, for the same reason.
  const rangeQuery = useQuery({
    queryKey: ['prices', 'range', selected?.id, index?.id ?? null, view.from, view.to, view.side, view.range],
    queryFn:
      selected && askedRange !== null
        ? async () => {
            const size = askedRange === 'auto' ? (autoSizes.current.get(autoKey) ?? null) : askedRange
            const answer = await api.priceRangeBars(selected.id, period, { side: view.side, size, under: index?.id ?? null })
            if (askedRange === 'auto') autoSizes.current.set(autoKey, answer.size)
            return answer
          }
        : skipToken,
    refetchInterval,
    placeholderData: keepPreviousData,
  })
  const chartQuery = ranged ? rangeQuery : timeQuery
  const contextQuery = useQuery({
    queryKey: ['prices', 'context', selected?.id, view.from, view.to],
    queryFn: selected ? () => api.priceContext(selected.id, period) : skipToken,
    refetchInterval,
    placeholderData: keepPreviousData,
  })

  // What is drawn is what the server last answered, which for a moment is the view before.
  const timeData = timeQuery.data
  const rangeData = rangeQuery.data
  const fresh = useMemo<ChartModel | undefined>(() => {
    if (ranged) return rangeData ? rangeChart(rangeData) : undefined
    return timeData ? timeChart(timeData, view.side) : undefined
  }, [ranged, rangeData, timeData, view.side])
  // The last chart drawn stays, dimmed, while the first range bars are on their way or
  // when a range is refused: a grey block in its place says less than the chart it hides.
  // Kept from one render to the next as React's own guide does it: set while rendering.
  const [last, setLast] = useState<ChartModel>()
  if (fresh && fresh !== last) setLast(fresh)
  const chart = fresh ?? last
  const stale = fresh === undefined && chart !== undefined

  const context = chart && contextQuery.data?.instrument.id === chart.instrument.id ? contextQuery.data : undefined
  // The signals can be left out of the chart: on a busy day they hide the prices they are about.
  const [signalsShown, setSignalsShown] = useState(rememberedSignals)
  const { markers, indexMarkers, hasTrades, hasSignals } = useMemo(() => {
    if (!chart || !context) return { markers: NO_MARKERS, indexMarkers: NO_MARKERS, hasTrades: false, hasSignals: false }
    // On the turbo's own chart a trade sits at the price paid or received.
    const trades = tradeMarkers(context.trades, chart.instrument.role === 'turbo', chart.locate)
    // Signals are about the index: on its line when it is there.
    const signals = signalsShown ? signalMarkers(context.signals, chart.indexLocate ?? chart.locate) : NO_MARKERS
    const drawn = { hasTrades: trades.length > 0, hasSignals: signals.length > 0 }
    if (chart.indexLocate) return { markers: trades, indexMarkers: signals, ...drawn }
    return { markers: [...trades, ...signals].sort((a, b) => a.time - b.time), indexMarkers: NO_MARKERS, ...drawn }
  }, [chart, context, signalsShown])

  const [hoveredTime, setHoveredTime] = useState<number | null>(null)
  const [refits, setRefits] = useState(0)
  const readTime = hoveredTime ?? chart?.lastTime ?? null
  const shownLabel = chart ? instrumentLabel(chart.instrument) : ''
  const sideLabel = chart?.side === 'ask' ? 'Ask' : 'Bid'
  // A price move is counted in the instrument's currency; an index moves in points.
  const unit = selected?.role === 'underlying' ? 'pts' : (selected?.currency ?? '')

  const chooseRange = (range: number | 'auto') => {
    if (selected) rememberRange(selected.role, range)
    if (range === 'auto') {
      // Asking for Auto is asking for the choice to be made again.
      autoSizes.current.delete(autoKey)
      if (view.range === 'auto') void rangeQuery.refetch()
    }
    show({ ...view, range })
  }
  // A range the server will not build (too many bars for the period) is an answer about
  // what was typed, said beside the fields: not a breakdown of the page.
  const refusal =
    ranged && rangeQuery.error instanceof ApiError && rangeQuery.error.status === 422 ? rangeQuery.error.message : undefined
  const built = useMemo<BuiltRange | undefined>(() => {
    // Refused: the fields keep what was asked, counted in the tick of the last chart of this instrument.
    if (refusal && typeof view.range === 'number' && chart && chart.instrument.id === selected?.id) {
      const tick = chart.kind === 'range' ? chart.tick : Number((10 ** -chart.decimals).toFixed(chart.decimals))
      return { tick, decimals: chart.decimals, ticks: ticksOf(view.range, tick), size: view.range }
    }
    if (fresh?.kind === 'range') return { tick: fresh.tick, decimals: fresh.decimals, ticks: fresh.ticks, size: fresh.size }
    return undefined
  }, [refusal, view.range, chart, fresh, selected?.id])

  return (
    <>
      <PageHeader
        title="Prices"
        description={`Bid and ask of the turbos and of their index, as the price recorder kept them every ten seconds. Times are in ${timezone}.`}
        actions={
          <>
            <PeriodFilter view={view} today={today} onChange={(from, to) => show(withPeriod(view, from, to))} />
            <Export chart={chart} />
          </>
        }
      />

      <QueryState
        isPending={instrumentsQuery.isPending}
        error={instrumentsQuery.error}
        hasData={!!instrumentsQuery.data}
      >
        {!selected ? (
          <NothingRecorded view={view} />
        ) : (
          <div className={cn('flex flex-col gap-6 transition-opacity', instrumentsQuery.isPlaceholderData && 'opacity-60')}>
            <div className="grid gap-6 xl:grid-cols-[19rem_minmax(0,1fr)]">
              <InstrumentPicker
                instruments={instruments}
                selectedId={selected.id}
                availability={availability}
                timeZone={timezone}
                onSelect={(instrument) =>
                  show({
                    ...view,
                    instrument: instrument.id,
                    // A range that suits an index means nothing on a turbo: each kind has its own.
                    range: !ranged || instrument.role === selected.role ? view.range : rememberedRange(instrument.role),
                  })
                }
              />

              <div className="flex min-w-0 flex-col gap-6">
                <Panel
                  title={instrumentLabel(selected)}
                  description={[selected.description, selected.symbol, selected.currency].filter(Boolean).join(' · ')}
                >
                  <div className="flex flex-col gap-4">
                    <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                      {/* Tighter on a phone, so that all seven fit one row with none of them out of sight:
                          a second row costs the chart forty pixels, and a row that scrolls hides "Range". */}
                      <div role="group" aria-label="Bars" className="flex flex-wrap gap-1 sm:gap-1.5">
                        {INTERVALS.map((seconds) => {
                          const possible = possibleIntervals.includes(seconds)
                          const active = !ranged && seconds === view.interval
                          return (
                            <Button
                              key={seconds}
                              variant={active ? 'primary' : 'outline'}
                              size="sm"
                              className="max-sm:px-1.5"
                              aria-pressed={active}
                              disabled={!possible}
                              title={possible ? undefined : 'Too many bars for this period: choose a shorter one'}
                              onClick={() => show({ ...view, interval: seconds, range: null })}
                            >
                              {intervalLabel(seconds)}
                            </Button>
                          )
                        })}
                        <Button
                          variant={ranged ? 'primary' : 'outline'}
                          size="sm"
                          className="max-sm:px-1.5"
                          aria-pressed={ranged}
                          onClick={() => {
                            if (!ranged) show({ ...view, range: rememberedRange(selected.role) })
                          }}
                        >
                          Range
                        </Button>
                      </div>
                      <div role="group" aria-label="Side of the quote" className="flex gap-1.5">
                        {SIDES.map((side) => (
                          <Button
                            key={side.id}
                            variant={side.id === view.side ? 'primary' : 'outline'}
                            size="sm"
                            aria-pressed={side.id === view.side}
                            onClick={() => show({ ...view, side: side.id })}
                          >
                            {side.label}
                          </Button>
                        ))}
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        leftIcon={<Maximize2 className="size-3.5" />}
                        onClick={() => setRefits((count) => count + 1)}
                      >
                        Whole period
                      </Button>
                    </div>

                    {ranged && (
                      <RangeControl
                        built={built}
                        unit={unit}
                        auto={view.range === 'auto'}
                        refusal={refusal}
                        onChange={chooseRange}
                      />
                    )}

                    <QueryState
                      isPending={chartQuery.isPending}
                      error={refusal ? null : chartQuery.error}
                      hasData={!!chart}
                      skeleton={<Skeleton className="h-[26rem] w-full rounded-xl" />}
                    >
                      {chart && readTime !== null && chart.hasCandles ? (
                        <div
                          className={cn(
                            'flex flex-col gap-3 transition-opacity',
                            (chartQuery.isPlaceholderData || stale) && 'opacity-60',
                          )}
                        >
                          {chart.kind === 'time' ? (
                            <PriceReadout
                              time={readTime}
                              bar={chart.barAt.get(readTime)}
                              decimals={chart.decimals}
                              isLast={hoveredTime === null}
                              indexName={chart.indexName}
                              indexBar={chart.indexBarAt.get(readTime)}
                              indexDecimals={chart.indexDecimals}
                            />
                          ) : (
                            <RangeReadout
                              row={chart.rowAt.get(readTime)}
                              time={readTime}
                              timeZone={chart.timeZone}
                              decimals={chart.decimals}
                              isLast={hoveredTime === null}
                              sideLabel={sideLabel}
                              indexName={chart.indexName}
                              indexDecimals={chart.indexDecimals}
                            />
                          )}
                          {/* Under the chart on a phone: the chart is what was come for. */}
                          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 max-sm:order-last">
                            <Legend indexName={chart.indexName} hasTrades={hasTrades} hasSignals={hasSignals} />
                            {/* With the legend, which says what is drawn, and not in the row of buttons above: one more
                                there wraps it. Not "Signals" alone: that is the name of a page in the menu. */}
                            <StretchSwitch
                              label="Signal marks"
                              checked={signalsShown}
                              onChange={(shown) => {
                                rememberSignals(shown)
                                setSignalsShown(shown)
                              }}
                              className="gap-2"
                            />
                          </div>
                          <PriceChart
                            // The chart draws on a canvas with the colours it read when it was made:
                            // another theme means another chart (only the zoom is lost).
                            key={theme}
                            candles={chart.candles}
                            markers={markers}
                            decimals={chart.decimals}
                            indexLine={chart.indexLine}
                            indexMarkers={indexMarkers}
                            indexDecimals={chart.indexDecimals}
                            showSeconds={chart.showSeconds}
                            viewKey={`${chart.key}|${refits}`}
                            onHover={setHoveredTime}
                            label={`${sideLabel} of ${shownLabel} in ${chart.barsLabel}. The same figures are in the table below.`}
                            className={chart.indexLine ? 'h-[30rem]' : 'h-[23rem]'}
                          />
                        </div>
                      ) : (
                        <p className="py-10 text-center text-sm text-muted-foreground">
                          {/* An instrument is listed because it has bars: with none on the ask, it is the ask that is missing. */}
                          {chart?.side === 'ask' && (chart.hasBars || chart.kind === 'range')
                            ? `No ask was recorded for ${shownLabel} in this period: the bid is what there is.`
                            : `No bar of ${shownLabel} in this period.`}
                        </p>
                      )}
                    </QueryState>

                    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                      <p className="text-xs text-muted-foreground">
                        Scroll or pinch to zoom, drag to move.{live ? ' Read again every 30 seconds.' : ''}
                      </p>
                      <ChartAttribution />
                    </div>
                    {ranged && (
                      <AboutRangeBars
                        tick={built ? built.tick.toFixed(built.decimals) : '0.01'}
                        side={view.side}
                        unit={unit}
                      />
                    )}
                  </div>
                </Panel>

                <GapsPanel
                  gaps={context?.gaps}
                  own={chart && availabilityOf(availability, chart.instrument)}
                  error={contextQuery.error}
                  timeZone={chart?.timeZone ?? timezone}
                />
              </div>
            </div>

            {chart?.kind === 'time' && <TimeBarsTable chart={chart} />}
            {chart?.kind === 'range' && <RangeBarsTable chart={chart} />}
          </div>
        )}
      </QueryState>
    </>
  )
}
