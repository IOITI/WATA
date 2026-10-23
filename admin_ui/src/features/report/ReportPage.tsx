import { useQuery } from '@tanstack/react-query'

import { BalanceChart } from '@/components/charts/PerformanceCharts'
import { KpiTile } from '@/components/KpiTile'
import { PageHeader, Panel } from '@/components/Panel'
import { PnlValue, QueryState } from '@/components/status'
import { StatCard } from '@/components/ui/stat-card'
import { api } from '@/lib/api'
import { formatDay, formatMoney, formatNumber, formatPercent } from '@/lib/format'
import type { MilestoneProjection, Report, TradeSummary } from '@/lib/types'

interface PeriodRow {
  label: string
  summary: TradeSummary
}

/** No losing trade makes the factor infinite, which the server sends as null. */
function profitFactor(summary: TradeSummary): string {
  if (summary.trade_count === 0) return '—'
  if (summary.profit_factor === null) return summary.wins > 0 ? 'no loss' : '—'
  return summary.profit_factor.toFixed(2)
}

function PeriodTable({ rows, periodHeader }: { rows: PeriodRow[]; periodHeader: string }) {
  if (rows.length === 0) {
    return <p className="py-4 text-center text-sm text-muted-foreground">No closed trade yet.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[44rem] text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">{periodHeader}</th>
            <th className="py-2 pr-4 text-right font-medium">Trades</th>
            <th className="py-2 pr-4 text-right font-medium">Won</th>
            <th className="py-2 pr-4 text-right font-medium">Net profit</th>
            <th className="py-2 pr-4 text-right font-medium">Compounded</th>
            <th className="py-2 pr-4 text-right font-medium">Profit factor</th>
            <th className="py-2 pr-4 text-right font-medium">Max drawdown</th>
            <th className="py-2 pr-4 text-right font-medium">Average win</th>
            <th className="py-2 text-right font-medium">Average loss</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {rows.map(({ label, summary }) => (
            <tr key={label}>
              <td className="py-2.5 pr-4 font-medium">{label}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{summary.trade_count}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">
                {summary.trade_count > 0 ? formatPercent(summary.win_rate, false, 0) : '—'}
              </td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={summary.net_profit} kind="money" />
              </td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={summary.compounded_percent} kind="percent" />
              </td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{profitFactor(summary)}</td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={summary.max_drawdown} kind="percent" />
              </td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={summary.avg_win} kind="percent" />
              </td>
              <td className="py-2.5 text-right">
                <PnlValue value={summary.avg_loss} kind="percent" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Milestones({ report }: { report: Report }) {
  const { weekly, monthly } = report.milestones
  if (report.current_balance === null) {
    return (
      <p className="text-sm text-muted-foreground">
        Projections start from the account value, which is recorded with each daily report. None has been recorded
        yet.
      </p>
    )
  }
  if (weekly.length === 0 && monthly.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No projection: the average weekly and monthly returns are not positive, or every milestone is already passed.
      </p>
    )
  }
  const monthlyByMilestone = new Map(monthly.map((projection) => [projection.milestone, projection]))
  const weeklyByMilestone = new Map(weekly.map((projection) => [projection.milestone, projection]))
  const milestones = [...new Set([...weekly, ...monthly].map((projection) => projection.milestone))].sort((a, b) => a - b)

  const cell = (projection: MilestoneProjection | undefined) =>
    projection ? (
      <>
        {formatDay(projection.date)}
        {!projection.within_horizon && <span className="text-muted-foreground"> (beyond 5 years)</span>}
      </>
    ) : (
      '—'
    )

  return (
    <table className="w-full text-left text-xs">
      <thead className="text-muted-foreground">
        <tr>
          <th className="py-2 pr-4 font-medium">Account value</th>
          <th className="py-2 pr-4 font-medium">At the average weekly return</th>
          <th className="py-2 font-medium">At the average monthly return</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border/40">
        {milestones.map((milestone) => (
          <tr key={milestone}>
            <td className="py-2.5 pr-4 font-medium tabular-nums">{formatMoney(milestone)}</td>
            <td className="py-2.5 pr-4">{cell(weeklyByMilestone.get(milestone))}</td>
            <td className="py-2.5">{cell(monthlyByMilestone.get(milestone))}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ReportContent({ report }: { report: Report }) {
  const cumulative: PeriodRow[] = Object.values(report.cumulative)
    .sort((a, b) => a.days - b.days)
    .map((period) => ({ label: `Last ${period.days} days`, summary: period }))
  const weeks: PeriodRow[] = report.last_10_weeks
    .filter((week) => week.trade_count > 0)
    .map((week) => ({ label: `${week.week_label} · from ${formatDay(week.week_start, false)}`, summary: week }))
  const months: PeriodRow[] = report.last_12_months.map((month) => ({
    label: `${month.month_label} ${month.year}`,
    summary: month,
  }))
  const years: PeriodRow[] = report.last_5_years.map((year) => ({ label: String(year.year), summary: year }))
  const today: PeriodRow[] = [
    { label: 'All', summary: report.daily_summary },
    { label: 'Long', summary: report.by_direction.long },
    { label: 'Short', summary: report.by_direction.short },
  ]

  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        {report.current_balance !== null && report.balance_history.length >= 2 ? (
          // Its value at each daily report, to scrub, and how far it moved since the first of them.
          <StatCard
            label="Account value"
            value={report.current_balance}
            trend={report.balance_history.map((point) => point.total_value)}
            trendLabels={report.balance_history.map((point) => formatDay(point.date_day))}
            previous={report.balance_history[0].total_value}
            period={`Since ${formatDay(report.balance_history[0].date_day)}, one value per daily report`}
            format={(value) => formatMoney(value)}
            color="var(--chart-series)"
          />
        ) : (
          <KpiTile
            label="Account value"
            value={formatMoney(report.current_balance)}
            hint={
              report.balance_recorded_on
                ? `Recorded with the daily report of ${formatDay(report.balance_recorded_on)}`
                : 'Recorded with each daily report; none yet'
            }
          />
        )}
        <KpiTile
          label="Winning days in a row"
          value={formatNumber(report.streak.current_streak)}
          hint={`Best run: ${report.streak.best_streak}`}
        />
        <KpiTile
          label="Average week"
          value={<PnlValue value={report.averages.weekly_percent} kind="percent" className="font-semibold" />}
          hint="Compounded return, weeks without a trade count as 0%"
        />
        <KpiTile
          label="Average month"
          value={<PnlValue value={report.averages.monthly_percent} kind="percent" className="font-semibold" />}
          hint={`Average year: ${formatPercent(report.averages.yearly_percent, true)}`}
        />
      </div>

      <Panel
        title="Account value"
        description="Total value at Saxo (cash and open positions), one point per daily report."
      >
        {report.balance_history.length >= 2 ? (
          <BalanceChart points={report.balance_history} />
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {report.balance_history.length === 0
              ? 'No value recorded yet: the first one comes with the next daily report.'
              : 'One value recorded so far: the curve starts with the second daily report.'}
          </p>
        )}
      </Panel>

      <Panel title="Today" bodyClassName="pt-2">
        <PeriodTable rows={today} periodHeader="Trades of the day" />
      </Panel>
      <Panel title="Recent periods" bodyClassName="pt-2">
        <PeriodTable rows={cumulative} periodHeader="Period" />
      </Panel>
      <Panel title="Weeks" description="The last ten weeks that had a closed trade." bodyClassName="pt-2">
        <PeriodTable rows={weeks} periodHeader="Week" />
      </Panel>
      <Panel title="Months" bodyClassName="pt-2">
        <PeriodTable rows={months} periodHeader="Month" />
      </Panel>
      <Panel title="Years" bodyClassName="pt-2">
        <PeriodTable rows={years} periodHeader="Year" />
      </Panel>

      <Panel
        title="Milestones"
        description="When the account would reach each milestone if it kept growing at its average return so far. A projection, not a forecast."
        bodyClassName="pt-3"
      >
        <Milestones report={report} />
      </Panel>
    </>
  )
}

export function ReportPage() {
  const reportQuery = useQuery({ queryKey: ['report'], queryFn: api.report, refetchInterval: 120_000 })
  const report = reportQuery.data

  return (
    <>
      <PageHeader
        title="Report"
        description={
          report
            ? `The figures of the daily Telegram report, as of ${formatDay(report.report_date)}.`
            : 'The figures of the daily Telegram report.'
        }
      />
      <QueryState isPending={reportQuery.isPending} error={reportQuery.error} hasData={!!report}>
        {report && <ReportContent report={report} />}
      </QueryState>
    </>
  )
}
