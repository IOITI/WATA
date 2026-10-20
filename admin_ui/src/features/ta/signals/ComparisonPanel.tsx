// A rule's signals of one day beside TradingView's (what the trader received for
// the rule's indice from anywhere but the engine): paired by action within a
// tolerance, listed, and marked on the day's prices.

import { useQuery } from '@tanstack/react-query'
import { ChartSpline } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'

import { Panel } from '@/components/Panel'
import { ActionBadge, QueryState } from '@/components/status'
import { Badge } from '@/components/ui/badge'
import { Input, Select } from '@/components/ui/form'
import { ChartAttribution } from '@/features/prices/ChartAttribution'
import { chartTime, priceDecimals, timeLocator } from '@/features/prices/chartData'
import { PriceChart } from '@/features/prices/PriceChart'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import type { TaRule } from '@/lib/types'

import { chartsPath } from '../chartLinks'
import { dayIn } from '../data/wataBars'
import { tickerText } from '../engine/tickers'
import { barsLabel, comparisonMarkers } from './ruleModel'

export function ComparisonPanel({ rules, timeZone }: { rules: TaRule[]; timeZone: string }) {
  const [ruleId, setRuleId] = useState(() => rules.find((rule) => rule.mode !== 'off')?.id ?? rules[0]?.id ?? '')
  const [today] = useState(() => dayIn(Date.now(), timeZone))
  const [day, setDay] = useState(today)
  const [tolerance, setTolerance] = useState('')
  const rule = rules.find((entry) => entry.id === ruleId) ?? null
  const toleranceSeconds = tolerance.trim() === '' ? undefined : Number(tolerance)

  const comparisonQuery = useQuery({
    queryKey: ['ta-comparison', ruleId, day, toleranceSeconds],
    queryFn: () => api.taComparison(ruleId, day, toleranceSeconds),
    enabled: Boolean(rule && day && (toleranceSeconds === undefined || toleranceSeconds >= 1)),
  })
  const barsQuery = useQuery({
    queryKey: ['ta-comparison-bars', rule?.asset.key, day],
    queryFn: () => api.assetBars(`capital/${rule!.asset.key}`, { from: day, to: day }, 60),
    enabled: Boolean(rule && day),
  })
  const comparison = comparisonQuery.data

  const chart = useMemo(() => {
    const bars = barsQuery.data?.bars ?? []
    const side = rule?.side === 'ask' ? 5 : 1
    const candles = bars
      .filter((bar) => bar[side] !== null)
      .map((bar) => ({
        time: chartTime(bar[0], timeZone),
        open: bar[side] as number,
        high: bar[side + 1] as number,
        low: bar[side + 2] as number,
        close: bar[side + 3] as number,
      }))
    const shown = new Set(candles.map((candle) => candle.time))
    return {
      candles,
      decimals: priceDecimals(bars),
      markers: comparison ? comparisonMarkers(comparison.rows, timeLocator(60, timeZone, shown)) : [],
    }
  }, [barsQuery.data, comparison, rule?.side, timeZone])

  if (rules.length === 0) return null
  const ticker = rule
    ? tickerText({
        provider: 'capital',
        key: rule.asset.key,
        side: rule.side,
        rangeTicks: rule.bars.type === 'range' ? rule.bars.ticks : null,
      })
    : null

  return (
    <Panel
      title="Beside TradingView"
      description="A rule’s signals of a day, paired with what the trader received for its indice from anywhere but the engine: TradingView’s alerts."
      action={
        rule && ticker ? (
          <Link
            to={chartsPath({ symbol: ticker, tf: rule.bars.type === 'time' ? rule.bars.timeframe : '1', script: rule.script.id })}
            className="inline-flex items-center gap-1.5 text-sm text-text-secondary underline-offset-4 hover:underline"
          >
            <ChartSpline className="size-4" /> Its script on the chart
          </Link>
        ) : null
      }
    >
      <div className="mb-4 flex flex-wrap items-end gap-3 text-sm">
        <label className="flex w-64 flex-col gap-1">
          <span className="text-xs text-text-secondary">Rule</span>
          <Select value={ruleId} onChange={(event) => setRuleId(event.target.value)} options={rules.map((entry) => ({ value: entry.id, label: entry.name }))} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-text-secondary">Day</span>
          <Input type="date" value={day} max={today} onChange={(event) => setDay(event.target.value || today)} />
        </label>
        <label className="flex w-40 flex-col gap-1">
          <span className="text-xs text-text-secondary">Within (seconds)</span>
          <Input
            type="number"
            min={1}
            max={3600}
            placeholder={comparison ? String(comparison.tolerance_seconds) : 'by its bars'}
            value={tolerance}
            onChange={(event) => setTolerance(event.target.value)}
          />
        </label>
      </div>
      <QueryState isPending={comparisonQuery.isPending} error={comparisonQuery.error} hasData={!!comparison}>
        {comparison && rule && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone="success">{comparison.paired} paired</Badge>
              <Badge tone={comparison.only_wata ? 'warning' : 'neutral'}>{comparison.only_wata} WATA only</Badge>
              <Badge tone={comparison.only_tradingview ? 'warning' : 'neutral'}>{comparison.only_tradingview} TradingView only</Badge>
              <span className="text-xs text-muted-foreground">
                {rule.asset.key} · {barsLabel(rule.bars)} · {comparison.indice}, paired within {comparison.tolerance_seconds} s of
                their bars’ open.
              </span>
            </div>
            {chart.candles.length > 0 && (
              <div className="space-y-1">
                <div className="h-80">
                  <PriceChart
                    candles={chart.candles}
                    markers={chart.markers}
                    decimals={chart.decimals}
                    showSeconds={false}
                    viewKey={`${ruleId}-${day}`}
                    onHover={() => undefined}
                    label={`${rule.asset.key} on ${day}, with WATA’s signals above the bars and TradingView’s below`}
                    className="h-full"
                  />
                </div>
                {/* The charting library's licence asks for its notice wherever its chart is shown. */}
                <ChartAttribution />
              </div>
            )}
            {comparison.rows.length === 0 ? (
              <p className="text-sm text-muted-foreground">No signal on either side that day.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-text-secondary">
                    <tr>
                      <th className="py-2 pr-3 font-medium">Bar</th>
                      <th className="py-2 pr-3 font-medium">Signal</th>
                      <th className="py-2 pr-3 font-medium">WATA</th>
                      <th className="py-2 pr-3 font-medium">TradingView</th>
                      <th className="py-2 font-medium">Apart</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {comparison.rows.map((row, index) => (
                      <tr key={index}>
                        <td className="whitespace-nowrap py-2 pr-3">{formatDateTime(row.at, timeZone)}</td>
                        <td className="py-2 pr-3">
                          <ActionBadge action={row.action} />
                        </td>
                        <td className="py-2 pr-3">
                          {row.wata ? (
                            <span>
                              {row.wata.alert_title ?? 'alert()'}{' '}
                              <span className="text-xs text-muted-foreground">({row.wata.outcome})</span>
                            </span>
                          ) : (
                            <span className="text-xs text-warning">none</span>
                          )}
                        </td>
                        <td className="py-2 pr-3">
                          {row.tradingview ? (
                            <span>
                              {formatDateTime(row.tradingview.signal_timestamp, timeZone)}{' '}
                              <span className="text-xs text-muted-foreground">({row.tradingview.outcome ?? 'not decided'})</span>
                            </span>
                          ) : (
                            <span className="text-xs text-warning">none</span>
                          )}
                        </td>
                        <td className="py-2 font-mono text-xs">{row.delta_seconds === null ? '' : `${row.delta_seconds} s`}</td>
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
