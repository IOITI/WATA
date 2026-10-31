import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, Badge, ButtonLink, PageHeader, Panel, QueryState } from 'ioiti-ui'
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

import { PaperDays, PaperReport, Reference } from './PaperParts'
import { LIVE_OWNER, paperRules, paperSectionId, paperTradingHref, reportState } from './paperModel'
import { BacktestsDisabled } from './parts'
import { usePaper } from './usePaper'

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
