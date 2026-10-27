import { Button, cn, ExportButton, Hint, Panel, QueryState, Skeleton, StretchSwitch, useTheme } from 'ioiti-ui'
import { Maximize2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { RangeBarsTable, TimeBarsTable } from '@/features/prices/BarsTable'
import { ChartAttribution } from '@/features/prices/ChartAttribution'
import {
  allowedIntervals,
  chartTime,
  daysBetween,
  instrumentLabel,
  intervalLabel,
  INTERVALS,
  signalMarkers,
  tradeMarkers,
} from '@/features/prices/chartData'
import type { ChartMarker, Side } from '@/features/prices/chartData'
import { rangeBarsCsv, timeBarsCsv } from '@/features/prices/chartModel'
import type { ChartModel } from '@/features/prices/chartModel'
import { GapsPanel } from '@/features/prices/GapsPanel'
import { rowVolume } from '@/features/prices/liveTail'
import { PriceChart } from '@/features/prices/PriceChart'
import { PriceReadout, RangeReadout } from '@/features/prices/PriceReadout'
import { RangeControl } from '@/features/prices/RangeControl'
import { rememberedRange, rememberRange } from '@/features/prices/rangeMemory'
import { rememberedSignals, rememberSignals } from '@/features/prices/signalMemory'
import type { ChartView } from '@/features/prices/view'
import type { InstrumentAvailability, PriceInstrument } from '@/lib/types'

import { useAssetChart } from './useAssetChart'
import { useLiveTail } from './useLiveTail'
import type { LiveStatus } from './useLiveTail'

/** Range bars are made of closed ten seconds: asked for again at most this often while the stream closes them. */
const RANGE_RELOAD_MS = 10_000

const NO_MARKERS: ChartMarker[] = []
const SIDES: { id: Side; label: string }[] = [
  { id: 'bid', label: 'Bid' },
  { id: 'ask', label: 'Ask' },
]

/**
 * Whether the chart follows the stream, and how late it is: Capital.com's
 * time of the last quote to now. Words beside the dot, never the dot alone.
 */
export function LiveBadge({ status }: { status: LiveStatus }) {
  let tone: 'live' | 'quiet' | 'off'
  let text: string
  if (!status.connected) {
    tone = 'off'
    text = 'Reconnecting…'
  } else if (!status.live) {
    tone = 'off'
    text = status.detail ? `Not live: ${status.detail}` : 'Not live'
  } else if (status.market === 'closed') {
    tone = 'quiet'
    text = 'Live · market closed'
  } else {
    tone = 'live'
    text = status.delayMs === null ? 'Live' : `Live · ${Math.round(status.delayMs)} ms`
  }
  return (
    <Hint content="Capital.com's time of the last quote to now, as the server's clock counts" wrap>
    <span
      role="status"
      aria-live="off"
      className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-0.5 text-xs tabular-nums"
    >
      <span
        aria-hidden
        className={cn(
          'size-1.5 rounded-full',
          tone === 'live' && 'animate-pulse bg-[var(--positive)]',
          tone === 'quiet' && 'bg-[var(--text-secondary)]',
          tone === 'off' && 'bg-[var(--warning)]',
        )}
      />
      {text}
    </span>
    </Hint>
  )
}

function Legend({
  indexName,
  hasTrades,
  hasSignals,
  hasVolume,
}: {
  indexName?: string
  hasTrades: boolean
  hasSignals: boolean
  hasVolume: boolean
}) {
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
      {hasVolume && (
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="h-3 w-1.5 rounded-[1px] opacity-40" style={{ backgroundColor: 'var(--chart-series)' }} />
          Quote changes, bottom
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

function Export({ chart, asset }: { chart: ChartModel | undefined; asset: PriceInstrument }) {
  const name = asset.symbol ?? (asset.uic === null ? asset.key : String(asset.uic))
  const timeCsv = useMemo(() => timeBarsCsv(chart?.timeZone ?? 'UTC'), [chart?.timeZone])
  const rangeCsv = useMemo(() => rangeBarsCsv(chart?.timeZone ?? 'UTC'), [chart?.timeZone])
  if (chart?.kind === 'range') {
    return <ExportButton name={`wata-prices-${name}-range-${chart.size}`} rows={chart.rows} columns={rangeCsv} />
  }
  return <ExportButton name={`wata-prices-${name}-${chart?.interval ?? 0}s`} rows={chart?.bars ?? []} columns={timeCsv} />
}

interface AssetChartProps {
  asset: PriceInstrument
  /** Under a turbo, its index, drawn in a panel below the prices. */
  index: PriceInstrument | null
  view: ChartView
  onViewChange: (view: ChartView) => void
  /** The period includes today and the asset is still recorded: read again every half minute. */
  live: boolean
  /** full: the chart of the asset's page; compact: that of an opened row of the Assets View. */
  variant?: 'full' | 'compact'
  /** What is missing of the asset in the period (recorded by Saxo), beside the recorder's own gaps. */
  availability?: InstrumentAvailability
  /** In place of the recorder's gaps, for a history with no recorder behind it: what was fetched of each day. */
  coverage?: ReactNode
}

/**
 * The chart of one asset, with what is needed to read it: the bars and their
 * length (or range bars), the side of the quote, a readout of the bar
 * pointed at, the trades and signals placed on it, and, in full, what was not
 * recorded and the bars as a table. Lightweight Charts' notice goes with it.
 */
export function AssetChart({
  asset,
  index,
  view,
  onViewChange,
  live,
  variant = 'full',
  availability,
  coverage,
}: AssetChartProps) {
  const { theme } = useTheme()
  const { timezone } = useSession()
  const full = variant === 'full'
  // Capital.com's prices come as a stream: the chart moves with each quote.
  const streaming = live && asset.provider === 'capital'
  const { chart, stale, ranged, chartQuery, contextQuery, context, refusal, built, chooseAutoAgain, prices, reload } = useAssetChart({
    asset,
    index,
    view,
    live,
    streaming,
  })
  const rangeReloadedAt = useRef(0)
  const { chart: liveChart, status: liveStatus } = useLiveTail({
    assetRef: streaming ? asset.ref : null,
    prices,
    side: view.side,
    timeZone: chart?.timeZone ?? timezone,
    onBar: () => {
      // Range bars are built from closed ten seconds, on the server.
      if (!ranged || Date.now() - rangeReloadedAt.current < RANGE_RELOAD_MS) return
      rangeReloadedAt.current = Date.now()
      void chartQuery.refetch()
    },
    onResync: () => void reload(),
  })
  // The quote changes of each bar, along the bottom: shown by default where they are the stream's own volume.
  const [volumeShown, setVolumeShown] = useState(asset.provider === 'capital')
  const volume = useMemo(
    () => (volumeShown && chart?.kind === 'time' ? chart.bars.map((row) => rowVolume(row, chart.timeZone)) : undefined),
    [volumeShown, chart],
  )
  const possibleIntervals = allowedIntervals(daysBetween(view.from, view.to))

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
  // Nothing pointed at, on a chart that follows the stream: the bar still moving.
  const liveRow = streaming && chart?.kind === 'time' && hoveredTime === null ? liveStatus.lastRow : null
  const readTime = hoveredTime ?? (liveRow && chart ? chartTime(liveRow[0], chart.timeZone) : chart?.lastTime) ?? null
  const shownLabel = chart ? instrumentLabel(chart.instrument) : instrumentLabel(asset)
  const sideLabel = chart?.side === 'ask' ? 'Ask' : 'Bid'
  // A price move is counted in the asset's currency; an index moves in points.
  const unit = asset.role === 'underlying' ? 'pts' : (asset.currency ?? '')

  const chooseRange = (range: number | 'auto') => {
    rememberRange(asset.role, range)
    if (range === 'auto') chooseAutoAgain()
    onViewChange({ ...view, range })
  }

  const controls = (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
      {/* Tighter on a phone, so that all seven fit one row with none of them out of sight:
          a second row costs the chart forty pixels, and a row that scrolls hides "Range". */}
      <div role="group" aria-label="Bars" className="flex flex-wrap gap-1 sm:gap-1.5">
        {INTERVALS.map((seconds) => {
          const possible = possibleIntervals.includes(seconds)
          const active = !ranged && seconds === view.interval
          const button = (
            <Button
              key={seconds}
              variant={active ? 'primary' : 'outline'}
              size="sm"
              className="max-sm:px-1.5"
              aria-pressed={active}
              disabled={!possible}
              onClick={() => onViewChange({ ...view, interval: seconds, range: null })}
            >
              {intervalLabel(seconds)}
            </Button>
          )
          // Disabled: its hint says why.
          return possible ? button : (
            <Hint key={seconds} content="Too many bars for this period: choose a shorter one" wrap>
              {button}
            </Hint>
          )
        })}
        {full && (
          <Button
            variant={ranged ? 'primary' : 'outline'}
            size="sm"
            className="max-sm:px-1.5"
            aria-pressed={ranged}
            onClick={() => {
              if (!ranged) onViewChange({ ...view, range: rememberedRange(asset.role) })
            }}
          >
            Range
          </Button>
        )}
      </div>
      <div role="group" aria-label="Side of the quote" className="flex gap-1.5">
        {SIDES.map((side) => (
          <Button
            key={side.id}
            variant={side.id === view.side ? 'primary' : 'outline'}
            size="sm"
            aria-pressed={side.id === view.side}
            onClick={() => onViewChange({ ...view, side: side.id })}
          >
            {side.label}
          </Button>
        ))}
      </div>
      <Button variant="ghost" size="sm" leftIcon={<Maximize2 className="size-3.5" />} onClick={() => setRefits((count) => count + 1)}>
        Whole period
      </Button>
      {streaming && <LiveBadge status={liveStatus} />}
      {full && (
        <div className="sm:ml-auto">
          <Export chart={chart} asset={asset} />
        </div>
      )}
    </div>
  )

  const drawing = (
    <QueryState
      isPending={chartQuery.isPending}
      error={refusal ? null : chartQuery.error}
      hasData={!!chart}
      skeleton={<Skeleton className={cn('w-full rounded-xl', full ? 'h-[26rem]' : 'h-[18rem]')} />}
    >
      {chart && readTime !== null && chart.hasCandles ? (
        <div className={cn('flex flex-col gap-3 transition-opacity', (chartQuery.isPlaceholderData || stale) && 'opacity-60')}>
          {chart.kind === 'time' ? (
            <PriceReadout
              time={readTime}
              bar={liveRow ?? chart.barAt.get(readTime)}
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
            <Legend indexName={chart.indexName} hasTrades={hasTrades} hasSignals={hasSignals} hasVolume={!!volume} />
            {/* With the legend, which says what is drawn, and not in the row of buttons above: one more
                there wraps it. Not "Signals" alone: that is the name of a page in the menu. */}
            {full && (
              <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                {chart.kind === 'time' && (
                  <StretchSwitch label="Volume" checked={volumeShown} onChange={setVolumeShown} className="gap-2" />
                )}
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
            )}
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
            volume={volume}
            live={streaming && chart.kind === 'time' ? liveChart : null}
            showSeconds={chart.showSeconds}
            viewKey={`${chart.key}|${refits}`}
            onHover={setHoveredTime}
            label={`${sideLabel} of ${shownLabel} in ${chart.barsLabel}.${full ? ' The same figures are in the table below.' : ''}`}
            className={
              full ? (chart.indexLine ? 'h-[30rem]' : 'h-[23rem]') : chart.indexLine ? 'h-[22rem]' : 'h-[17rem]'
            }
          />
        </div>
      ) : (
        <p className="py-10 text-center text-sm text-muted-foreground">
          {/* An asset is listed because it has bars: with none on the ask, it is the ask that is missing. */}
          {chart?.side === 'ask' && (chart.hasBars || chart.kind === 'range')
            ? `No ask was recorded for ${shownLabel} in this period: the bid is what there is.`
            : `No bar of ${shownLabel} in this period.`}
        </p>
      )}
    </QueryState>
  )

  const footer = (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <p className="text-xs text-muted-foreground">
        Scroll or pinch to zoom, drag to move.
        {streaming
          ? ranged
            ? ' Range bars are built again as the stream closes each ten seconds.'
            : ' Follows Capital.com’s stream: each quote moves the last bar.'
          : live
            ? ' Read again every 30 seconds.'
            : ''}
      </p>
      <ChartAttribution />
    </div>
  )

  if (!full) {
    return (
      <div className="flex min-w-0 flex-col gap-3">
        {controls}
        {drawing}
        {footer}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title={instrumentLabel(asset)}
        description={[asset.description, asset.symbol, asset.currency].filter(Boolean).join(' · ')}
      >
        <div className="flex flex-col gap-4">
          {controls}
          {ranged && (
            <RangeControl built={built} unit={unit} auto={view.range === 'auto'} refusal={refusal} onChange={chooseRange} />
          )}
          {drawing}
          {footer}
          {ranged && <AboutRangeBars tick={built ? built.tick.toFixed(built.decimals) : '0.01'} side={view.side} unit={unit} />}
        </div>
      </Panel>

      {coverage ?? (
        <GapsPanel
          gaps={context?.gaps}
          own={availability}
          error={contextQuery.error}
          timeZone={chart?.timeZone ?? timezone}
          hours={asset.provider === 'capital' ? 'the market’s opening hours' : undefined}
        />
      )}

      {chart?.kind === 'time' && <TimeBarsTable chart={chart} />}
      {chart?.kind === 'range' && <RangeBarsTable chart={chart} />}
    </div>
  )
}
