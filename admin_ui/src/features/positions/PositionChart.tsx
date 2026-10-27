import { useQuery } from '@tanstack/react-query'
import { QueryState, useTheme } from 'ioiti-ui'
import { useMemo } from 'react'

import { ChartAttribution } from '@/features/prices/ChartAttribution'
import { timeChart } from '@/features/prices/chartModel'
import { PriceChart } from '@/features/prices/PriceChart'
import { api } from '@/lib/api'
import type { Position } from '@/lib/types'

import { positionFocus, positionMarkers, positionOverlays, reasonLevel } from './positionMarks'

/**
 * A closed position on the chart of its turbo, as the price recorder kept it, with the index below: the signal
 * that opened it, where it was bought and sold, and its levels while it was held. As a backtest's trade is drawn.
 */
export function PositionChart({ position }: { position: Position }) {
  const { theme } = useTheme()
  const refsQuery = useQuery({
    queryKey: ['position-chart', position.position_id],
    queryFn: () => api.positionChart(position.position_id),
    staleTime: Infinity,
  })
  const refs = refsQuery.data
  const chartQuery = useQuery({
    queryKey: ['position-chart-bars', refs?.turbo, refs?.index, refs?.day],
    enabled: !!refs?.turbo && !!refs?.day,
    staleTime: 10 * 60_000,
    queryFn: async () => {
      const period = { from: refs!.day!, to: refs!.day! }
      const [prices, index] = await Promise.all([
        api.assetBars(refs!.turbo!, period, 10),
        refs!.index ? api.assetBars(refs!.index, period, 10).catch(() => null) : Promise.resolve(null),
      ])
      return timeChart({ prices, index }, 'bid')
    },
  })
  const chart = chartQuery.data
  const signal = refs?.signal ?? null
  const markers = useMemo(() => (chart ? positionMarkers(position, signal, chart.locate) : []), [chart, position, signal])
  const overlays = useMemo(
    () => (chart?.kind === 'time' ? positionOverlays(position, chart.bars, chart.timeZone) : []),
    [chart, position],
  )
  const focus = chart ? positionFocus(position, signal, chart.locate) : null
  const level = reasonLevel(position.position_close_reason, position.position_open_price)

  if (refsQuery.isError) return null
  if (refs && !refs.turbo) {
    return <p className="text-xs text-muted-foreground">Its turbo was not recorded by the price recorder: no chart of it.</p>
  }
  return (
    <QueryState isPending={refsQuery.isPending || chartQuery.isPending} error={chartQuery.error} hasData={!!chart}>
      {chart && (
        <div className="flex flex-col gap-2">
          <PriceChart
            // The chart draws with the colours it read when it was made: another theme, another chart.
            key={theme}
            candles={chart.candles}
            markers={markers}
            decimals={Math.max(chart.decimals, 2)}
            indexLine={chart.indexLine}
            indexDecimals={chart.indexDecimals}
            overlays={overlays}
            showSeconds
            viewKey={`${chart.key}|${position.position_id}`}
            focus={focus}
            onHover={() => undefined}
            label={`The bid of ${position.instrument_symbol ?? position.instrument_name ?? 'the turbo'}, with the index below`}
            className="h-[22rem]"
          />
          <p className="text-xs text-muted-foreground">
            Its bid every ten seconds as the price recorder kept it, the index below. The solid line is the price paid; the
            dashed ones its best and worst while held{level ? `, and the line of its close: ${level.label.toLowerCase()}` : ''}.
            {signal ? ' The first arrow is the signal that opened it.' : ''}
          </p>
          <ChartAttribution />
        </div>
      )}
    </QueryState>
  )
}
