import { keepPreviousData, skipToken, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useRef, useState } from 'react'

import { ticksOf } from '@/features/prices/chartData'
import { rangeChart, timeChart } from '@/features/prices/chartModel'
import type { ChartModel, TimeBars } from '@/features/prices/chartModel'
import type { BuiltRange } from '@/features/prices/RangeControl'
import type { ChartView } from '@/features/prices/view'
import { api, ApiError } from '@/lib/api'
import type { PriceInstrument } from '@/lib/types'

/** How often a period that includes today is read again. A bar closes every ten seconds; nobody watches that closely. */
export const REFRESH_MS = 30_000
/** How long the bars of a past period are kept without asking again: they no longer change. */
const PAST_FRESH_MS = 10 * 60_000

interface AssetChartOptions {
  asset: PriceInstrument
  /** Under a turbo, its index, drawn in a panel below the prices. */
  index: PriceInstrument | null
  view: ChartView
  /** The period includes today and the asset is still recorded: read again every half minute. */
  live: boolean
}

/**
 * What a chart of an asset needs from the server, and what it draws of it:
 * the bars of the view (or its range bars), and what goes beside them. The
 * queries are named after the asset's ref and never after anything a live
 * event makes out of date: an event must not have every chart read again.
 */
export function useAssetChart({ asset, index, view, live }: AssetChartOptions) {
  const period = useMemo(() => ({ from: view.from, to: view.to }), [view.from, view.to])
  const refetchInterval = live ? REFRESH_MS : false
  // Range bars (a bar ends when the price has moved enough) or bars of a fixed length.
  const ranged = view.range !== null

  // The bars of one asset, shared between charts: the index under one turbo is the index under the next.
  const queryClient = useQueryClient()
  const loadBars = (target: PriceInstrument) =>
    queryClient.fetchQuery({
      queryKey: ['asset-bars', target.ref, view.from, view.to, view.interval],
      queryFn: () => api.assetBars(target.ref, period, view.interval),
      staleTime: live ? REFRESH_MS / 2 : PAST_FRESH_MS,
    })
  // The prices and their index are asked for together and arrive together. Given one
  // after the other, the chart would for a moment hold two panels that do not go
  // with each other, and the charting library does not recover from that.
  const timeQuery = useQuery({
    queryKey: ['asset-chart', asset.ref, index?.ref ?? null, view.from, view.to, view.interval],
    queryFn: !ranged
      ? async (): Promise<TimeBars> => {
          const [prices, indexPrices] = await Promise.all([loadBars(asset), index ? loadBars(index) : null])
          return { prices, index: indexPrices }
        }
      : skipToken,
    refetchInterval,
    placeholderData: keepPreviousData,
  })
  // In Auto the server chooses the range from what the price covered in the period. Asked
  // again at each refresh of a day still going, it would sooner or later choose another one
  // and redraw the whole chart with other bars under the reader's eyes. So the choice is
  // made once for a view (an asset, a period, a side) and asked for by name afterwards.
  const autoSizes = useRef(new Map<string, number>())
  const autoKey = `${asset.ref}|${view.from}|${view.to}|${view.side}`
  const askedRange = view.range
  // Range bars come with the index at the end of each of them: one answer, for the same reason.
  const rangeQuery = useQuery({
    queryKey: ['asset-range', asset.ref, index?.ref ?? null, view.from, view.to, view.side, view.range],
    queryFn:
      askedRange !== null
        ? async () => {
            const size = askedRange === 'auto' ? (autoSizes.current.get(autoKey) ?? null) : askedRange
            const answer = await api.assetRangeBars(asset.ref, period, { side: view.side, size, under: index?.id ?? null })
            if (askedRange === 'auto') autoSizes.current.set(autoKey, answer.size)
            return answer
          }
        : skipToken,
    refetchInterval,
    placeholderData: keepPreviousData,
  })
  const chartQuery = ranged ? rangeQuery : timeQuery
  const contextQuery = useQuery({
    queryKey: ['asset-context', asset.ref, view.from, view.to],
    queryFn: () => api.assetContext(asset.ref, period),
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

  const context = chart && contextQuery.data?.instrument.ref === chart.instrument.ref ? contextQuery.data : undefined

  // A range the server will not build (too many bars for the period) is an answer about
  // what was typed, said beside the fields: not a breakdown of the page.
  const refusal =
    ranged && rangeQuery.error instanceof ApiError && rangeQuery.error.status === 422 ? rangeQuery.error.message : undefined
  const built = useMemo<BuiltRange | undefined>(() => {
    // Refused: the fields keep what was asked, counted in the tick of the last chart of this asset.
    if (refusal && typeof view.range === 'number' && chart && chart.instrument.ref === asset.ref) {
      const tick = chart.kind === 'range' ? chart.tick : Number((10 ** -chart.decimals).toFixed(chart.decimals))
      return { tick, decimals: chart.decimals, ticks: ticksOf(view.range, tick), size: view.range }
    }
    if (fresh?.kind === 'range') return { tick: fresh.tick, decimals: fresh.decimals, ticks: fresh.ticks, size: fresh.size }
    return undefined
  }, [refusal, view.range, chart, fresh, asset.ref])

  /** Asking for Auto is asking for the choice to be made again. */
  const chooseAutoAgain = () => {
    autoSizes.current.delete(autoKey)
    if (view.range === 'auto') void rangeQuery.refetch()
  }

  return { chart, fresh, stale, ranged, chartQuery, contextQuery, context, refusal, built, chooseAutoAgain }
}
