// A chart the agent drew under its answer (show_chart), from figures its tools gave: a line over time or bars by
// category, four series at most in the design's colours, its references as horizontal lines, and where its
// figures come from under it. Drawn with ioiti-ui's charts, as the admin's own are.

import { BarChart, LineChart } from 'ioiti-ui'

import type { AgentChart as Chart } from '@/lib/types'

import { chartPoints, chartValue } from './model'

const HEIGHT = 220

export function AgentChart({ chart }: { chart: Chart }) {
  const format = (value: number) => chartValue(value, chart.unit)
  return (
    <figure className="glass-card space-y-2 rounded-lg border border-border p-3">
      <figcaption className="text-sm font-medium">{chart.title}</figcaption>
      {chart.kind === 'line' ? (
        <LineChart
          ariaLabel={chart.title}
          data={chartPoints(chart)}
          series={chart.series.map((series) => ({ key: series.key, label: series.label }))}
          references={chart.references.map((reference) => ({ value: reference.value, label: reference.label ?? undefined }))}
          zero={chart.zero ?? false}
          connectGaps={false}
          height={HEIGHT}
          timeZone="UTC"
          formatValue={format}
          showLegend={chart.series.length > 1}
        />
      ) : (
        <BarChart
          ariaLabel={chart.title}
          data={(chart.categories ?? []).map((category) => ({ ...category }))}
          series={chart.series.map((series) => ({ key: series.key, label: series.label }))}
          tone={chart.tone ?? 'series'}
          height={HEIGHT}
          formatValue={format}
          showLegend={chart.series.length > 1}
        />
      )}
      <p className="text-xs text-muted-foreground">From: {chart.source}</p>
    </figure>
  )
}
