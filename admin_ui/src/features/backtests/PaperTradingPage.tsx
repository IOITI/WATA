import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
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
  PageHeader,
  Panel,
  QueryState,
  SignedValue,
} from 'ioiti-ui'
import { FileText, RotateCcw } from 'lucide-react'
import { useEffect } from 'react'
import { Link, Navigate, useLocation, useParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { ClaudeBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { LivePaperPanel } from '@/features/performance/LivePaperPanel'
import { madeByClaude } from '@/features/system/claude'
import { barsLabel, MODE_LABEL, MODE_TONE } from '@/features/ta/signals/ruleModel'
import { RulePeekName } from '@/features/ta/signals/RulePeek'
import { api } from '@/lib/api'
import type { BacktestPaperDay, BacktestPaperEntry, BacktestPaperReference } from '@/lib/types'

import { STATE_LABEL } from './model'
import {
  anyUnderWay,
  LIVE_OWNER,
  paperReportHref,
  paperRules,
  paperSectionId,
  paperTradingHref,
  reportPeriod,
  reportState,
  versionsSentence,
} from './paperModel'
import { BacktestsDisabled, OlderCodeBadge } from './parts'

/** The paper trading of every rule, read again every few seconds while a report is being made. */
function usePaper(enabled: boolean) {
  return useQuery({
    queryKey: ['backtest-paper'],
    queryFn: api.backtestPaper,
    enabled,
    refetchInterval: (query) => (anyUnderWay(query.state.data) ? 5_000 : false),
  })
}

/** An owner's report since it began: its figures, where it stands, and the way to it. */
function PaperReport({
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
function Reference({ reference, ruleId }: { reference: BacktestPaperReference | null; ruleId: string }) {
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
function PaperDays({ days, madeAgain }: { days: BacktestPaperDay[]; madeAgain: boolean }) {
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

/**
 * Paper trading, in the Backtests section: each rule in shadow and the signals WATA received, traded each finished
 * day by a run of their own, and their report, one run of every day since they began, as any backtest is read.
 */
export function PaperTradingPage() {
  const { backtests_enabled: enabled } = useSession()
  const paperQuery = usePaper(enabled)
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, enabled })
  const paper = paperQuery.data
  const rules = rulesQuery.data?.rules
  const shown = rules ? paperRules(rules, paper) : []

  // An address that names a rule's place (#rule-…, #live) is brought to it once they are drawn.
  const { hash } = useLocation()
  const drawn = Boolean(paper && rules)
  useEffect(() => {
    if (drawn && hash) document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.()
  }, [drawn, hash])

  return (
    <>
      <PageHeader
        title="Paper trading"
        description="Each finished weekday, once the market is closed, every signal rule in shadow is traded on the signals it raised, and the signals WATA received under the live settings, as WATA would have traded them. Nothing here trades."
      />
      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState isPending={paperQuery.isPending || rulesQuery.isPending} error={paperQuery.error ?? rulesQuery.error} hasData={drawn}>
          <div className="flex flex-col gap-6">
            <p className="text-sm text-muted-foreground">
              A day on paper is a run of its own, from an account of its own. A report is one run of every day since its rule was made
              (or since the signals received were first traded on paper), its account carried from one day to the next: every trade
              is on its page. Each report is made again after each finished day, without random entries; “Run again with changes”
              on its page makes a run with them.
            </p>
            {shown.length === 0 && (
              <Alert tone="neutral">
                <AlertDescription>
                  No rule is in shadow: paper trading starts the evening after one is put in shadow on the{' '}
                  <Link className="font-medium underline-offset-4 hover:underline" to="/ta/signals">
                    Signal rules
                  </Link>{' '}
                  page.
                </AlertDescription>
              </Alert>
            )}
            {shown.map(({ rule, entry }) => (
              <Panel
                key={rule.id}
                id={paperSectionId(rule.id)}
                title={
                  <span className="flex flex-wrap items-center gap-2">
                    <RulePeekName id={rule.id} name={rule.name} />
                    <Badge tone={MODE_TONE[rule.mode]}>{MODE_LABEL[rule.mode]}</Badge>
                    {madeByClaude(rule) && <ClaudeBadge />}
                  </span>
                }
                description={`${rule.indice} · ${rule.asset.key}, ${barsLabel(rule.bars)}`}
              >
                <div className="flex flex-col gap-5">
                  <PaperReport owner={rule.id} name={rule.name} entry={entry} inShadow={rule.mode === 'shadow'} canAsk={rule.mode === 'shadow'} />
                  <section aria-label={`Days on paper of ${rule.name}`} className="space-y-2">
                    <h3 className="text-sm font-medium">Its days on paper</h3>
                    <Reference reference={entry?.reference ?? null} ruleId={rule.id} />
                    <PaperDays days={entry?.days ?? []} madeAgain={rule.mode === 'shadow'} />
                  </section>
                </div>
              </Panel>
            ))}
            <Panel
              id={paperSectionId(LIVE_OWNER)}
              title="The signals WATA received"
              description="TradingView’s alerts, as the trader received them, traded on paper under the live settings: each day beside the real one."
            >
              <PaperReport
                owner={LIVE_OWNER}
                name="the signals WATA received"
                entry={paper?.live ?? undefined}
                inShadow
                canAsk={Boolean(paper?.live && (paper.live.days.length > 0 || paper.live.report))}
              />
            </Panel>
            <LivePaperPanel enabled={enabled} />
          </div>
        </QueryState>
      )}
    </>
  )
}

/** The newest report of a rule (or of the signals received), whatever its run is tonight: a bookmark keeps working. */
export function PaperReportPage() {
  const { owner = '' } = useParams()
  const { backtests_enabled: enabled } = useSession()
  const paperQuery = usePaper(enabled)
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, enabled })
  const entry = owner === LIVE_OWNER ? paperQuery.data?.live : paperQuery.data?.rules[owner]
  const rule = rulesQuery.data?.rules.find((item) => item.id === owner)
  const report = entry?.report

  if (report) return <Navigate to={`/backtests/${report.run_id}`} replace />
  if (paperQuery.data && rulesQuery.data && owner !== LIVE_OWNER && !rule && !entry) {
    return (
      <NotFoundState
        title="No such rule on paper"
        description="No signal rule has this address: it may have been deleted, or the address is mistyped."
        suggestions={[
          { label: 'Paper trading', href: '/backtests/paper', description: 'Every rule traded on paper' },
          { label: 'Signal rules', href: '/ta/signals', description: 'The rules the TA engine runs' },
        ]}
      />
    )
  }
  const name = owner === LIVE_OWNER ? 'the signals WATA received' : (rule?.name ?? 'this rule')
  return (
    <>
      <PageHeader title="Paper report" description={`The report of ${name} since it began.`} parents={[{ label: 'Paper trading', href: '/backtests/paper' }]} />
      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState isPending={paperQuery.isPending || rulesQuery.isPending} error={paperQuery.error ?? rulesQuery.error} hasData={Boolean(paperQuery.data)}>
          <Panel>
            <div className="space-y-3 text-sm">
              <p>{reportState(entry ?? undefined, owner === LIVE_OWNER || rule?.mode === 'shadow').text}</p>
              <ButtonLink to={paperTradingHref(owner)} variant="outline" size="sm">
                Back to paper trading
              </ButtonLink>
            </div>
          </Panel>
        </QueryState>
      )}
    </>
  )
}
