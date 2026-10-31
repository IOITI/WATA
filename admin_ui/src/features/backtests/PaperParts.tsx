// A rule's paper trading, as Backtests › Paper trading and a rule's own page draw it (SignalRulePage.tsx): its report
// since it began, the run of reference its days are set against, and its days on paper one by one.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Badge,
  Button,
  ButtonLink,
  formatDateTime,
  formatDay,
  formatNumber,
  formatPercent,
  Hint,
  KpiGrid,
  KpiTile,
  SignedValue,
} from 'ioiti-ui'
import { FileText, RotateCcw } from 'lucide-react'
import { Link } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { BacktestPaperDay, BacktestPaperEntry, BacktestPaperReference } from '@/lib/types'

import { STATE_LABEL } from './model'
import { paperReportHref, reportPeriod, reportState, versionsSentence } from './paperModel'
import { OlderCodeBadge } from './parts'

/** An owner's report since it began: its figures, where it stands, and the way to it. */
export function PaperReport({
  owner,
  name,
  entry,
  inShadow,
  canAsk,
}: {
  owner: string
  name: string
  entry: BacktestPaperEntry | undefined
  inShadow: boolean
  canAsk: boolean
}) {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const ask = useMutation({
    mutationFn: () => api.askPaperReport(owner),
    onSuccess: (paper) => {
      queryClient.setQueryData(['backtest-paper'], paper)
      void queryClient.invalidateQueries({ queryKey: ['live-paper'] })
    },
  })
  const report = entry?.report ?? null
  const state = reportState(entry, inShadow)
  const headline = report?.headline ?? null
  const versions = versionsSentence(report?.versions)
  return (
    <section aria-label={`Report of ${name}`} className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium">{report ? `Report, ${reportPeriod(report)}` : 'Report'}</h3>
          <p className={state.tone === 'warning' ? 'text-xs text-warning' : 'text-xs text-muted-foreground'}>
            {state.text}
            {report?.made_at && ` Made ${formatDateTime(report.made_at, timezone, false)}.`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canAsk && (
            <Hint content={state.underWay ? 'Its report is being made already' : 'Made now, to the last finished day, rather than tonight'} wrap={state.underWay}>
              <Button
                variant="outline"
                size="sm"
                leftIcon={<RotateCcw className="size-3.5" />}
                disabled={state.underWay}
                isLoading={ask.isPending}
                onClick={() => ask.mutate()}
              >
                Make it now
              </Button>
            </Hint>
          )}
          <ButtonLink to={report ? paperReportHref(owner) : null} size="sm" leftIcon={<FileText className="size-3.5" />}>
            Open the report
          </ButtonLink>
        </div>
      </div>
      {ask.error && (
        <p role="alert" className="text-sm text-negative">
          {ask.error.message}
        </p>
      )}
      {report && (
        <KpiGrid columns={4}>
          <KpiTile
            label="All its days"
            value={<SignedValue value={headline?.total_percent ?? null} kind="percent" />}
            hint={headline?.profit_loss !== null && headline?.profit_loss !== undefined ? <SignedValue value={headline.profit_loss} kind="money" /> : 'compounded'}
          />
          <KpiTile
            label="Trades"
            value={formatNumber(headline?.trades ?? null)}
            hint={headline?.win_rate !== null && headline?.win_rate !== undefined ? `${formatPercent(headline.win_rate, false, 0)} won` : undefined}
          />
          <KpiTile label="Worst drawdown" value={<SignedValue value={headline?.worst_drawdown ?? null} kind="percent" />} />
          <KpiTile
            label="Days traded"
            value={formatNumber(headline?.days_tested ?? null)}
            hint={report.signals !== null ? `${formatNumber(report.signals)} signal${report.signals === 1 ? '' : 's'}` : undefined}
          />
        </KpiGrid>
      )}
      {versions && <p className="text-xs text-muted-foreground">{versions}</p>}
    </section>
  )
}

function referenceText(reference: BacktestPaperReference | null): string | null {
  if (!reference) return null
  if (reference.low === null) return `has ${reference.days} day${reference.days === 1 ? '' : 's'}: too few for a range.`
  return `made ${formatPercent(reference.low, true)} to ${formatPercent(reference.high, true)} on 90 % of its days.`
}

/** A rule's run of reference, which its days on paper are set against, or the way to make one. */
export function Reference({ reference, ruleId }: { reference: BacktestPaperReference | null; ruleId: string }) {
  const text = referenceText(reference)
  if (!reference || !text) {
    return (
      <p className="text-xs text-muted-foreground">
        No reference run: <Link className="font-medium text-foreground underline-offset-4 hover:underline" to={`/backtests/new?rule=${ruleId}`}>start a run from this rule</Link>,
        then set it as the rule’s reference on its page.
      </p>
    )
  }
  return (
    <p className="text-xs text-muted-foreground">
      Its reference,{' '}
      <Link className="font-medium text-foreground underline-offset-4 hover:underline" to={`/backtests/${reference.run_id}`}>
        {reference.name ?? 'a run made from it'}
      </Link>
      , {text}
    </p>
  )
}

function dayResult(day: BacktestPaperDay) {
  if (day.state === 'no_entry') return <span className="text-muted-foreground">No entry</span>
  if (day.state !== 'done') return <span className="text-muted-foreground">{STATE_LABEL[day.state]}</span>
  if (day.day_percent === null) return <span className="text-muted-foreground">—</span>
  return <SignedValue value={day.day_percent} kind="percent" />
}

/**
 * Each finished day traded on paper by a run of its own, newest first; one made with older backtest code says so,
 * and is made again while the rule is in shadow (`madeAgain`).
 */
export function PaperDays({ days, madeAgain }: { days: BacktestPaperDay[]; madeAgain: boolean }) {
  if (days.length === 0) return <p className="py-4 text-center text-sm text-muted-foreground">No day on paper yet: the first comes once the market is closed.</p>
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[26rem] text-left text-xs">
        <caption className="sr-only">Its days on paper, newest first</caption>
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">Day</th>
            <th className="py-2 pr-4 text-right font-medium">On paper</th>
            <th className="py-2 pr-4 text-right font-medium">Trades</th>
            <th className="py-2 text-right font-medium">Against its reference</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {days.map((day) => (
            <tr key={day.day}>
              <td className="py-2.5 pr-4 font-medium">
                <span className="inline-flex items-center gap-2">
                  {day.run_id ? (
                    <Link className="underline-offset-2 hover:underline" to={`/backtests/${day.run_id}`}>
                      {formatDay(day.day)}
                    </Link>
                  ) : (
                    formatDay(day.day)
                  )}
                  {day.stale && <OlderCodeBadge madeWith={day.made_with} madeAgain={madeAgain} />}
                </span>
              </td>
              <td className="py-2.5 pr-4 text-right">{dayResult(day)}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{day.trades ?? '—'}</td>
              <td className="py-2.5 text-right">
                {day.outside === null ? <span className="text-muted-foreground">—</span> : day.outside ? <Badge tone="warning">Outside</Badge> : <Badge tone="neutral">Inside</Badge>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
