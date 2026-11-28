import { useQuery } from '@tanstack/react-query'
import { ButtonLink, formatDay, formatMoney, formatNumber, formatPercent, KpiGrid, KpiTile, PageHeader, Panel, QueryState, SignedValue, StatCard, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from 'ioiti-ui'
import { useSearchParams } from 'react-router'

import { BalanceChart } from '@/components/charts/PerformanceCharts'
import { AgentSheet } from '@/features/agent/AgentSheet'
import { AgentVerdict } from '@/features/agent/AgentVerdict'
import { AskAgentLink } from '@/features/agent/AskAgentLink'
import { api } from '@/lib/api'
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
    <Table density="compact" className="min-w-[44rem]">
      <TableHead>
        <TableRow>
          <TableHeaderCell>{periodHeader}</TableHeaderCell>
          <TableHeaderCell numeric>Trades</TableHeaderCell>
          <TableHeaderCell numeric>Won</TableHeaderCell>
          <TableHeaderCell numeric>Net profit</TableHeaderCell>
          <TableHeaderCell numeric>Compounded</TableHeaderCell>
          <TableHeaderCell numeric>Profit factor</TableHeaderCell>
          <TableHeaderCell numeric>Max drawdown</TableHeaderCell>
          <TableHeaderCell numeric>Average win</TableHeaderCell>
          <TableHeaderCell numeric>Average loss</TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map(({ label, summary }) => (
          <TableRow key={label}>
            <TableCell className="font-medium">{label}</TableCell>
            <TableCell numeric>{summary.trade_count}</TableCell>
            <TableCell numeric>
              {summary.trade_count > 0 ? formatPercent(summary.win_rate, false, 0) : '—'}
            </TableCell>
            <TableCell className="text-right">
              <SignedValue value={summary.net_profit} kind="money" />
            </TableCell>
            <TableCell className="text-right">
              <SignedValue value={summary.compounded_percent} kind="percent" />
            </TableCell>
            <TableCell numeric>{profitFactor(summary)}</TableCell>
            <TableCell className="text-right">
              <SignedValue value={summary.max_drawdown} kind="percent" />
            </TableCell>
            <TableCell className="text-right">
              <SignedValue value={summary.avg_win} kind="percent" />
            </TableCell>
            <TableCell className="text-right">
              <SignedValue value={summary.avg_loss} kind="percent" />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
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
    <Table density="compact">
      <TableHead>
        <TableRow>
          <TableHeaderCell>Account value</TableHeaderCell>
          <TableHeaderCell>At the average weekly return</TableHeaderCell>
          <TableHeaderCell>At the average monthly return</TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {milestones.map((milestone) => (
          <TableRow key={milestone}>
            <TableCell className="font-medium tabular-nums">{formatMoney(milestone)}</TableCell>
            <TableCell>{cell(weeklyByMilestone.get(milestone))}</TableCell>
            <TableCell>{cell(monthlyByMilestone.get(milestone))}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function ReportContent({ report, past = false }: { report: Report; past?: boolean }) {
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
      <KpiGrid>
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
          value={<SignedValue value={report.averages.weekly_percent} kind="percent" className="font-semibold" />}
          hint="Compounded return, weeks without a trade count as 0%"
        />
        <KpiTile
          label="Average month"
          value={<SignedValue value={report.averages.monthly_percent} kind="percent" className="font-semibold" />}
          hint={`Average year: ${formatPercent(report.averages.yearly_percent, true)}`}
        />
      </KpiGrid>

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

      <Panel title={past ? `The day of ${formatDay(report.report_date)}` : 'Today'} bodyClassName="pt-2">
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

/** The day the address names (the AI agent links to a day so), or null for today's report. */
function reportDay(params: URLSearchParams): string | null {
  const day = params.get('day')
  return day && /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : null
}

export function ReportPage() {
  const [params] = useSearchParams()
  const day = reportDay(params)
  const reportQuery = useQuery({ queryKey: ['report', day], queryFn: () => api.report(day ?? undefined), refetchInterval: day ? false : 120_000 })
  const report = reportQuery.data
  const shown = report?.report_date.slice(0, 10)

  return (
    <>
      <PageHeader
        title="Report"
        description={
          report
            ? `The figures of the daily Telegram report, as of ${formatDay(report.report_date)}.`
            : 'The figures of the daily Telegram report.'
        }
        actions={report ? (
          <>
            {day && <ButtonLink href="/report" size="sm" variant="ghost">Today&apos;s report</ButtonLink>}
            <AskAgentLink subject={{ kind: 'day', id: report.report_date.slice(0, 10) }} label="Ask about the day" />
          </>
        ) : undefined}
      />
      {shown && <AgentVerdict about={`day:${shown}`} className="mb-4" />}
      <QueryState isPending={reportQuery.isPending} error={reportQuery.error} hasData={!!report}>
        {report && <ReportContent report={report} past={!!day} />}
      </QueryState>
      <AgentSheet />
    </>
  )
}
