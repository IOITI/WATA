import { useQuery } from '@tanstack/react-query'
import { CheckIcon, CheckList, Panel } from 'ioiti-ui'

import { api } from '@/lib/api'
import type { BacktestRun } from '@/lib/types'

import { newestModelCheck, newestStress, runChecks, verdictOf } from './verdict'
import type { Derived } from './verdict'

/**
 * Whether a run can be believed, at the top of its page: each check of the bar
 * Claude's research is held to (verdict.ts), each leading to the part of the
 * page that says more. Reads the runs made from it as the panels below do,
 * through the same queries.
 */
export function VerdictPanel({ run }: { run: BacktestRun }) {
  const listQuery = useQuery({ queryKey: ['backtests'], queryFn: api.backtests })
  const runs = listQuery.data?.items ?? []
  const stress = newestStress(runs, run.id)
  const stressQuery = useQuery({
    queryKey: ['backtest-sweep', stress?.id],
    queryFn: () => api.backtestSweep(stress?.id ?? ''),
    enabled: stress?.status.state === 'done',
    staleTime: Infinity,
  })
  const check = run.turbos === 'modelled' ? newestModelCheck(runs) : null
  const checkQuery = useQuery({ queryKey: ['backtest', check?.id], queryFn: () => api.backtest(check?.id ?? ''), enabled: Boolean(check) })
  const sweepQuery = useQuery({
    queryKey: ['backtest-sweep', run.id],
    queryFn: () => api.backtestSweep(run.id),
    enabled: run.kind === 'sweep',
    staleTime: Infinity,
  })

  const derived: Derived = {
    stress: stress ? { run: stress, sweep: stressQuery.data ?? null } : null,
    modelCheck: check ? { run: check, found: checkQuery.data?.summary?.model_check ?? null } : null,
    sweep: sweepQuery.data ?? null,
  }
  const checks = runChecks(run, derived)
  if (checks.length === 0) return null
  const verdict = verdictOf(checks)

  return (
    <Panel title="Can it be believed?" description="The bar Claude’s research is held to, checked on this run. Each check leads to what says more of it.">
      <div className="flex flex-col gap-4">
        <p className="flex items-start gap-2 text-sm font-medium">
          <CheckIcon state={verdict.tone} named={false} />
          {verdict.sentence}
        </p>
        <CheckList checks={checks} label="Checks" hrefOf={(check) => `#${check.section}`} className="md:grid-cols-2" />
      </div>
    </Panel>
  )
}

/** A part of a long run's page, as the nav at its top lists it (each an anchor the checks above lead to). */
export interface PageSection {
  id: string
  label: string
}
