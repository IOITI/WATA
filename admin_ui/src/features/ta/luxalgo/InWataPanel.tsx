// What WATA knows of an indicator of LuxAlgo's Library (src/admin/luxalgo_knowledge.py): the scripts taken from it,
// their backtests and the verdicts on them, the signal rules running them and their days on paper, and what WATA's
// check found, with "Check it". The agent's verdict on the indicator itself is the page's (AgentVerdict).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, formatDateTime, formatPercent, Hint, Panel, useToast } from 'ioiti-ui'
import { Link } from 'react-router'

import { ClaudeBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { VERDICT_SAID, verdictTone } from '@/features/agent/model'
import { MODE_LABEL, rulePath } from '@/features/ta/signals/ruleModel'
import { api } from '@/lib/api'
import type { LuxAlgoKnown, LuxAlgoVerdict } from '@/lib/types'

import { COMPAT_LABEL, COMPAT_TONE, compatSaid, knowsNothing } from './luxalgoModel'
import { useIndicatorCheck, useLuxAlgoChecks } from './useLuxAlgo'

function Verdict({ verdict }: { verdict?: LuxAlgoVerdict | null }) {
  if (!verdict) return null
  return (
    <Hint content={verdict.line} wrap>
      <Badge tone={verdictTone(verdict.tone)}>{VERDICT_SAID[verdict.tone]}</Badge>
    </Hint>
  )
}

export function InWataPanel({ slug, known }: { slug: string; known: LuxAlgoKnown }) {
  const { timezone } = useSession()
  const toast = useToast()
  const queryClient = useQueryClient()
  const checks = useLuxAlgoChecks()
  const compat = useIndicatorCheck(slug, known.compat)
  const available = !!checks.data?.checks.available
  const queued = !!checks.data?.checks.queued?.includes(slug) || checks.data?.checks.checking === slug
  const check = useMutation({
    mutationFn: () => api.checkLuxalgo([slug]),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['luxalgo', 'compat'] })
      toast({ title: 'Asked to be checked', description: 'Once no Pine study runs: its line here says what came of it.' })
    },
  })
  const checkButton = (
    <Button variant="outline" size="sm" disabled={!available || queued} isLoading={check.isPending} onClick={() => check.mutate()}>
      {queued ? 'Waiting for its check' : compat ? 'Check it again' : 'Check it'}
    </Button>
  )

  return (
    <Panel
      title="In WATA"
      description={knowsNothing({ ...known, compat }) ? 'Nothing yet: take it as your script, or ask the AI agent to test it.' : undefined}
      action={available ? checkButton : <Hint content="The checks need the Pine studies, which this admin does not run" wrap>{checkButton}</Hint>}
    >
      <div className="space-y-4 text-sm">
        {compat && (
          <p className="flex flex-wrap items-center gap-2">
            <Badge tone={COMPAT_TONE[compat.state]} dot>
              {COMPAT_LABEL[compat.state]}
            </Badge>
            <span className="text-text-secondary">
              {compatSaid(compat)} Checked {formatDateTime(compat.checked_at, timezone)}.
            </span>
          </p>
        )}
        {known.scripts.length > 0 && (
          <section>
            <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Scripts taken from it</h3>
            <ul className="flex flex-col gap-1">
              {known.scripts.map((script) => (
                <li key={script.id}>
                  <Link to={`/ta/indicators/${script.id}`} className="underline-offset-4 hover:underline">
                    {script.name}
                  </Link>
                  {script.made_by === 'claude' && <ClaudeBadge className="ml-2" />}
                </li>
              ))}
            </ul>
          </section>
        )}
        {known.runs.length > 0 && (
          <section>
            <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Their backtests</h3>
            <ul className="flex flex-col gap-1">
              {known.runs.map((run) => (
                <li key={run.id} className="flex flex-wrap items-center gap-2">
                  <Link to={`/backtests/${run.id}`} className="underline-offset-4 hover:underline">
                    {run.name ?? run.id}
                  </Link>
                  {typeof run.headline?.total_percent === 'number' && (
                    <span className="tabular-nums text-text-secondary">{formatPercent(run.headline.total_percent, true)}</span>
                  )}
                  <Verdict verdict={run.verdict} />
                </li>
              ))}
              {!!known.more_runs && <li className="text-muted-foreground">and {known.more_runs} more</li>}
            </ul>
          </section>
        )}
        {known.rules.length > 0 && (
          <section>
            <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Signal rules running them</h3>
            <ul className="flex flex-col gap-1">
              {known.rules.map((rule) => (
                <li key={rule.id} className="flex flex-wrap items-center gap-2">
                  <Link to={rulePath(rule.id)} className="underline-offset-4 hover:underline">
                    {rule.name}
                  </Link>
                  <Badge variant="outline">{MODE_LABEL[rule.mode]}</Badge>
                  {rule.paper.days > 0 && (
                    <span className="text-text-secondary">
                      {rule.paper.days} day{rule.paper.days === 1 ? '' : 's'} on paper
                      {rule.paper.outside ? `, ${rule.paper.outside} outside its reference's range` : ''}
                    </span>
                  )}
                  <Verdict verdict={rule.verdict} />
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </Panel>
  )
}
