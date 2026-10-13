import type { ReactNode } from 'react'

import { DistributionChart } from '@/components/charts/PerformanceCharts'
import { Panel } from '@/components/Panel'
import { PnlValue } from '@/components/status'
import { formatNumber, formatPercent } from '@/lib/format'
import type { BacktestFigures } from '@/lib/types'

import { distribution, RANDOM_VERDICT, randomSentence, randomVerdict, roomSentences } from './model'

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="text-xs font-medium text-muted-foreground">{children}</h3>
}

function Measure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{children}</dd>
    </div>
  )
}

function AgainstRandom({ figures }: { figures: BacktestFigures }) {
  const found = figures.against_random
  if (!found) {
    return (
      <p className="mt-2 text-sm text-muted-foreground">
        Nothing to compare with: no entry was asked for on these days, or the random entries were left out of this run.
      </p>
    )
  }
  const bins = distribution(found.totals, figures.total_percent)
  return (
    <div className="mt-2 flex flex-col gap-3">
      <p>
        <span className="text-2xl font-semibold tabular-nums">{formatPercent(found.beaten, false, 0)}</span>{' '}
        <span className="text-sm text-muted-foreground">of the random runs did worse than this one</span>
      </p>
      <p className="text-sm">{RANDOM_VERDICT[randomVerdict(found.beaten)]}</p>
      <DistributionChart bins={bins} counted="Random runs" own={figures.total_percent} ownLabel="This run" />
      <p className="text-xs leading-relaxed text-muted-foreground">
        {randomSentence(found)} A random entry is ended by WATA&apos;s rules only: it has no signal that closes it.
      </p>
    </div>
  )
}

/**
 * What the total of a run is worth: where it stands among entries taken at
 * random, what a trade brings, how much the total owes to the days there
 * were, and what the trades say of a stop and a target.
 */
export function TrustPanel({ figures }: { figures: BacktestFigures }) {
  if (figures.sample === undefined) {
    return (
      <Panel title="How far to trust these figures">
        <p className="text-sm text-muted-foreground">
          This run was made before a backtest was set against random entries. Run it again to have that, and the range
          of its total.
        </p>
      </Panel>
    )
  }
  const range = figures.total_range
  const room = roomSentences(figures)
  return (
    <Panel
      title="How far to trust these figures"
      description="WATA's exits decide most of a trade. A total says something of the entries only where random ones do worse."
    >
      <div className="grid gap-8 lg:grid-cols-2">
        <section aria-label="Against random entries" className="min-w-0">
          <Heading>Against random entries</Heading>
          <AgainstRandom figures={figures} />
        </section>

        <div className="flex min-w-0 flex-col gap-6">
          <section aria-label="Per trade">
            <Heading>Per trade</Heading>
            <dl className="mt-2 flex flex-col gap-1.5">
              <Measure label="On average, wins and losses together">
                <PnlValue value={figures.expectancy} kind="percent" />
              </Measure>
              <Measure label="Won for 1 lost">
                {figures.profit_factor === null || figures.profit_factor === undefined
                  ? figures.trades > 0
                    ? 'no loss'
                    : '-'
                  : formatNumber(figures.profit_factor, 2)}
              </Measure>
              <Measure label="Most trades lost in a row">{formatNumber(figures.longest_losing_streak)}</Measure>
              <Measure label="Days won, days lost">
                {formatNumber(figures.winning_days)}, {formatNumber(figures.losing_days)}
              </Measure>
              <Measure label="A day in the middle, and how far days spread">
                {formatPercent(figures.median_day, true)}, ±{formatNumber(figures.day_spread, 2)}
              </Measure>
            </dl>
          </section>

          <section aria-label="The total with other days">
            <Heading>The total with other days of the same kind</Heading>
            {range ? (
              <p className="mt-2 text-sm leading-relaxed">
                Drawing {figures.days} days again from the ones tested, {range.level} totals in 100 fall between{' '}
                <PnlValue value={range.low} kind="percent" /> and <PnlValue value={range.high} kind="percent" />.{' '}
                {formatPercent(range.losing_share, false, 0)} of the draws end in a loss.{' '}
                <span className="text-muted-foreground">It knows nothing of days unlike any of these.</span>
              </p>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">Too few days to draw a range from.</p>
            )}
          </section>

          <section aria-label="Room for a stop and a target">
            <Heading>Room for a stop and a target</Heading>
            {room.length > 0 ? (
              <ul className="mt-2 flex flex-col gap-1.5 text-sm leading-relaxed">
                {room.map((sentence) => (
                  <li key={sentence}>{sentence}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">No trade to read it from.</p>
            )}
          </section>
        </div>
      </div>
    </Panel>
  )
}
