import { CircleCheck } from 'lucide-react'
import { useMemo } from 'react'

import { Panel } from '@/components/Panel'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDateTime, formatDuration, formatTime } from '@/lib/format'
import type { InstrumentAvailability, PriceGap, PriceStretch } from '@/lib/types'

const REASONS: Record<PriceGap['reason'] | PriceStretch['reason'], string> = {
  down: 'Recorder not running',
  silent: 'No quote arriving',
  unquoted: 'No quote for this instrument',
  stopped: 'No more quote for this instrument',
}

interface GapsPanelProps {
  /** undefined while loading; null when the period is too long for the gaps to be worked out. */
  gaps: PriceGap[] | null | undefined
  /**
   * What is missing from the instrument shown alone: the times Saxo sent no
   * quote for it while the recorder was receiving others.
   */
  own?: InstrumentAvailability
  /** Why the gaps, the trades and the signals could not be read. */
  error?: Error | null
  timeZone: string
  /** When a missing bar counts: WATA's trading hours, or a market's own hours (Capital.com). */
  hours?: string
}

/**
 * What is missing from the recording, during trading hours: a backtest must
 * know where its data has holes. The recorder's own gaps, which every
 * instrument has, and those of the instrument shown.
 */
export function GapsPanel({ gaps: recorded, own, error, timeZone, hours = 'trading hours' }: GapsPanelProps) {
  const gaps = useMemo<(PriceGap | PriceStretch)[] | null | undefined>(
    () => recorded && [...recorded, ...(own?.stretches ?? [])].sort((a, b) => a.start.localeCompare(b.start)),
    [recorded, own],
  )
  const total = (gaps ?? []).reduce((seconds, gap) => seconds + gap.seconds, 0)
  // Only the longest stretches of an instrument are given: say so when there are more.
  const left = own ? own.stretch_count - own.stretches.length : 0

  return (
    <Panel
      title="Recording gaps"
      description={
        gaps && gaps.length > 0
          ? `${gaps.length} in ${hours}, ${formatDuration(total * 1000)} in all. No bar exists for these times.${
              left > 0 ? ` ${left} shorter ones of this instrument are not listed.` : ''
            }`
          : `Times in ${hours} for which no bar exists.`
      }
      bodyClassName="pt-3"
    >
      {gaps === undefined && error ? (
        <p role="alert" className="text-sm text-negative">
          The gaps, and the trades and signals on the chart, could not be loaded: {error.message}
        </p>
      ) : gaps === undefined ? (
        <Skeleton className="h-5 w-64" />
      ) : gaps === null ? (
        <p className="text-sm text-muted-foreground">Not worked out for a period longer than 31 days.</p>
      ) : gaps.length === 0 ? (
        <p className="flex items-center gap-2 text-sm">
          <CircleCheck aria-hidden className="size-4 text-positive" />
          Recorded without interruption during trading hours.
        </p>
      ) : (
        <div className="max-h-64 overflow-y-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">From</th>
                <th className="py-2 pr-4 font-medium">To</th>
                <th className="py-2 pr-4 text-right font-medium">Lasted</th>
                <th className="py-2 font-medium">Why</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {gaps.map((gap) => (
                <tr key={`${gap.start}|${gap.reason}`}>
                  <td className="py-2.5 pr-4 tabular-nums">{formatDateTime(gap.start, timeZone)}</td>
                  <td className="py-2.5 pr-4 tabular-nums">{formatTime(gap.end, timeZone)}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{formatDuration(gap.seconds * 1000)}</td>
                  <td className="py-2.5">{REASONS[gap.reason] ?? gap.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
