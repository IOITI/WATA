// What the editor's test found, under the editor across the page: the day drawn
// with the script's plots and alerts, every alert of the day (a click brings it
// into view), the plots' last values and what PineTS warned of.

import { useMemo, useState } from 'react'

import { Panel } from '@/components/Panel'
import { useTheme } from '@/components/theme-context'
import { ChartAttribution } from '@/features/prices/ChartAttribution'
import { PriceChart } from '@/features/prices/PriceChart'

import { dayIn } from '../data/wataBars'
import { tickerText } from '../engine/tickers'
import { timeframeOf } from '../engine/timeframes'
import { alertFocus, testChart } from './testChart'
import type { TestDone } from './TestPanel'

function formatValue(value: number | boolean | string | null): string {
  if (value === null) return 'na'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4)
  return String(value)
}

export function TestResult({ run, scriptName, timeZone }: { run: TestDone; scriptName: string; timeZone: string }) {
  const response = run.response
  const { theme } = useTheme()
  const chart = useMemo(
    () =>
      testChart(
        { candles: run.candles, plots: response.plots, alerts: response.alerts, day: run.day },
        timeZone,
        (ms) => dayIn(ms, timeZone),
      ),
    [run, response, timeZone],
  )
  const [focus, setFocus] = useState<{ from: number; to: number } | null>(null)
  const alertsOfDay = response.alerts.filter((alert) => dayIn(alert.time, timeZone) === run.day)
  const seconds = timeframeOf(run.timeframe)?.seconds ?? 60
  const timeOf = (ms: number) =>
    new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(ms)
  const marked = new Map(chart.markers.map((marker) => [marker.id, marker.time]))

  return (
    <Panel
      title={`Test of ${scriptName || 'the script'}`}
      description={`${tickerText(run.ticker)} on ${run.day}: ${response.bars} bars run in ${response.milliseconds} ms, the day before warming the indicators up.`}
    >
      <div className="flex flex-col gap-4 text-xs" data-testid="test-result">
        {chart.candles.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <PriceChart
              // The chart draws with the colours it read when it was made: another theme, another chart.
              key={theme}
              candles={chart.candles}
              markers={chart.markers}
              overlays={chart.overlays}
              decimals={chart.decimals}
              showSeconds={run.ticker.rangeTicks !== null || seconds < 60}
              viewKey={`${tickerText(run.ticker)}|${run.timeframe}|${run.day}|${response.bars}`}
              focus={focus}
              onHover={() => undefined}
              label={`${tickerText(run.ticker)} on ${run.day}, with the script's plots and its alerts`}
              className="h-[26rem]"
            />
            {(chart.drawn.length > 0 || chart.apart.length > 0) && (
              <ul className="flex flex-wrap items-center gap-x-4 gap-y-1" aria-label="Plots">
                {chart.drawn.map((entry) => (
                  <li key={entry.title} className="flex items-center gap-1.5">
                    <span aria-hidden className="h-0.5 w-4 rounded-full" style={{ background: `var(--chart-compare-${entry.tone.slice(-1)})` }} />
                    {entry.title}
                  </li>
                ))}
                {chart.apart.length > 0 && (
                  <li className="text-muted-foreground">Not drawn over the prices: {chart.apart.join(', ')}.</li>
                )}
              </ul>
            )}
            <ChartAttribution />
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <h3 className="mb-1 font-medium">
              Alerts on {run.day} ({alertsOfDay.length})
            </h3>
            {alertsOfDay.length === 0 ? (
              <p className="text-muted-foreground">None.</p>
            ) : (
              <ul className="max-h-72 divide-y divide-border overflow-auto rounded-lg border border-border">
                {alertsOfDay.map((alert, index) => {
                  const at = marked.get(`alert-${response.alerts.indexOf(alert)}`)
                  return (
                    <li key={`${alert.time}:${index}`}>
                      <button
                        type="button"
                        className="flex w-full flex-col gap-0.5 px-2 py-1.5 text-left hover:bg-surface-hover disabled:cursor-default"
                        disabled={at === undefined}
                        title={at === undefined ? undefined : 'Show it on the chart'}
                        onClick={() => at !== undefined && setFocus(alertFocus(chart, at))}
                      >
                        <span>
                          <span className="font-mono">{timeOf(alert.time)}</span> {alert.title ?? alert.type}
                        </span>
                        {alert.message && <span className="break-all font-mono text-muted-foreground">{alert.message}</span>}
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
          <div>
            <h3 className="mb-1 font-medium">Last values</h3>
            <table className="w-full">
              <tbody>
                {response.plots
                  .filter((plot) => !plot.title.startsWith('__'))
                  .map((plot) => (
                    <tr key={plot.title} className="border-b border-border/60 last:border-0">
                      <td className="py-1 pr-2">{plot.title}</td>
                      <td className="py-1 text-right font-mono">{formatValue(plot.last)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>

        {response.warnings.length > 0 && (
          <div>
            <h3 className="mb-1 font-medium text-warning">Warnings</h3>
            <ul className="list-disc pl-4">
              {response.warnings.slice(0, 10).map((warning, index) => (
                <li key={index}>
                  Bar {warning.bar}: {warning.message}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  )
}
