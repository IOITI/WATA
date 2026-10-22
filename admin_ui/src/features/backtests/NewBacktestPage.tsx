import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'

import { PageHeader, Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { StepPanels, Stepper } from '@/components/ui/stepper'
import { useSession } from '@/features/auth/auth-context'
import { todayIn, shiftDay } from '@/features/prices/chartData'
import { useScriptInputs } from '@/features/ta/signals/useScriptInputs'
import type { RuleDraft } from '@/features/ta/signals/ruleModel'
import { api } from '@/lib/api'
import type { BacktestOptions, BacktestRun, DataAsset, TaRule, TaScriptSummary } from '@/lib/types'

import { backtestProblems, draftOfRun, newBacktestDraft, periodDays, periodLimit, requestOf, strategyAssets, withRule } from './form'
import type { BacktestDraft } from './form'
import { AssetStep, PeriodStep, ResetLink, ReviewStep, SettingsStep, StrategyStep } from './formSteps'
import { BacktestsDisabled } from './parts'

const STEPS = [
  { title: 'Asset', description: 'and its bars' },
  { title: 'Strategy', description: 'what makes the signals' },
  { title: 'Period', description: 'days recorded' },
  { title: 'Settings', description: 'WATA’s rules' },
  { title: 'Review' },
]

/** A script asked for by its page (?script=<id>&version=<n>): one that runs, at a version it has. */
interface AskedScript {
  id: string | null
  version: number | null
}

function initialDraft(
  options: BacktestOptions,
  scripts: TaScriptSummary[],
  assets: DataAsset[],
  timezone: string,
  again: BacktestRun | undefined,
  rule: TaRule | undefined,
  asked: AskedScript = { id: null, version: null },
): BacktestDraft {
  if (again) return draftOfRun(again, options)
  const today = todayIn(timezone)
  const asset = strategyAssets(assets).find((entry) => entry.value.startsWith('capital/'))?.value ?? strategyAssets(assets)[0]?.value ?? ''
  const runnable = scripts.filter((entry) => entry.kind !== 'library')
  const script = runnable.find((entry) => entry.id === asked.id) ?? runnable[0]
  const draft = newBacktestDraft(options, script, asset, { from: shiftDay(today, -6), to: today })
  const version = script && script.id === asked.id && asked.version && script.versions.some((entry) => entry.number === asked.version) ? asked.version : null
  const pinned = version ? { ...draft, rule: { ...draft.rule, version } } : draft
  return rule ? withRule(pinned, rule) : pinned
}

export function NewBacktestPage() {
  const { timezone, backtests_enabled: enabled } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const againId = params.get('again')
  const ruleId = params.get('rule')
  const asked: AskedScript = { id: params.get('script'), version: Number(params.get('version')) || null }

  const optionsQuery = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, enabled, staleTime: 60_000 })
  const scriptsQuery = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts, enabled })
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, enabled })
  const assetsQuery = useQuery({ queryKey: ['data-assets', 'all'], queryFn: () => api.dataAssets('all'), enabled })
  const againQuery = useQuery({
    queryKey: ['backtest', againId],
    queryFn: () => api.backtest(againId as string),
    enabled: enabled && againId !== null,
  })
  const options = optionsQuery.data
  const scripts = scriptsQuery.data?.items
  const assets = assetsQuery.data?.items
  const rules = rulesQuery.data?.rules ?? []
  const loaded = options && scripts && assets && (againId === null || againQuery.data) && (ruleId === null || rulesQuery.data)

  const [draft, setDraft] = useState<BacktestDraft | null>(null)
  // Made once everything it is made of has arrived: set while rendering, as React's own guide does it.
  if (draft === null && loaded) {
    setDraft(initialDraft(options, scripts, assets, timezone, againQuery.data, rules.find((rule) => rule.id === ruleId), asked))
  }
  const [step, setStep] = useState(0)
  const [invalidInputs, setInvalidInputs] = useState<Record<string, string>>({})
  const update = (changes: Partial<BacktestDraft>) => setDraft((current) => (current ? { ...current, ...changes } : current))
  const updateRule = (changes: Partial<RuleDraft>) =>
    setDraft((current) => (current ? { ...current, rule: { ...current.rule, ...changes } } : current))

  const inputs = useScriptInputs(draft?.sourceKind === 'pine' ? (draft?.rule.scriptId ?? '') : '', draft?.rule.version ?? 1)
  const periodOk = Boolean(draft && draft.from && draft.to && draft.to >= draft.from && options && periodDays(draft.from, draft.to) <= periodLimit(draft, options))
  const coverageQuery = useQuery({
    queryKey: ['backtest-coverage', draft?.from, draft?.to, draft?.indice, draft?.asset, draft?.sourceKind],
    queryFn: () =>
      api.backtestCoverage({
        from: draft!.from, to: draft!.to, indice: draft!.indice, asset: draft!.asset || undefined, source: draft!.sourceKind,
      }),
    // Made-up turbos are made on any day of the index: nothing recorded to count.
    enabled: enabled && periodOk && draft?.turbos !== 'modelled',
    staleTime: 60_000,
  })

  const create = useMutation({
    mutationFn: () => api.createBacktest(requestOf(draft!, options!, inputs.meta)),
    onSuccess: async (run) => {
      await queryClient.invalidateQueries({ queryKey: ['backtests'] })
      navigate(`/backtests/${run.id}`)
    },
  })

  const problems = draft && options ? [
    ...backtestProblems(draft, options, inputs.meta),
    ...(Object.keys(invalidInputs).length > 0 ? ['An input holds a value its type does not take.'] : []),
  ] : []

  return (
    <>
      <PageHeader
        title={againId ? 'Backtest, again' : 'New backtest'}
        description="A strategy traded on the turbos the price recorder kept, as the trader and the position monitor trade."
      />
      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState
          isPending={!loaded}
          error={optionsQuery.error ?? scriptsQuery.error ?? assetsQuery.error ?? againQuery.error}
          hasData={draft !== null}
        >
          {draft && options && scripts && assets && (
            <Panel>
              <div className="space-y-6">
                <Stepper steps={STEPS} current={step} onStepChange={setStep} />
                <StepPanels current={step}>
                  <AssetStep
                    draft={draft}
                    update={update}
                    updateRule={updateRule}
                    assets={assets}
                    rules={rules}
                    onRule={(rule) => setDraft(withRule(draft, rule))}
                  />
                  <StrategyStep
                    draft={draft}
                    update={update}
                    updateRule={updateRule}
                    scripts={scripts}
                    options={options}
                    inputs={inputs}
                    invalidInputs={invalidInputs}
                    setInvalidInputs={setInvalidInputs}
                  />
                  <PeriodStep draft={draft} update={update} options={options} coverage={coverageQuery.data} coveragePending={coverageQuery.isFetching} />
                  <SettingsStep draft={draft} update={update} options={options} meta={inputs.meta} />
                  <ReviewStep draft={draft} update={update} options={options} scripts={scripts} coverage={coverageQuery.data} problems={problems} />
                </StepPanels>
                {create.error && (
                  <Alert tone="danger" role="alert">
                    <AlertTitle>Not asked for</AlertTitle>
                    <AlertDescription>{create.error.message}</AlertDescription>
                  </Alert>
                )}
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <ResetLink
                    onClick={() => {
                      setDraft(initialDraft(options, scripts, assets, timezone, undefined, undefined))
                      setStep(0)
                    }}
                  />
                  <div className="flex gap-2">
                    <Button variant="ghost" size="sm" disabled={step === 0} onClick={() => setStep(step - 1)}>
                      Back
                    </Button>
                    {step < STEPS.length - 1 ? (
                      <Button size="sm" onClick={() => setStep(step + 1)}>
                        Next
                      </Button>
                    ) : (
                      <Button size="sm" disabled={problems.length > 0} isLoading={create.isPending} onClick={() => create.mutate()}>
                        {draft.sweep ? 'Start the sweep' : 'Start the backtest'}
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            </Panel>
          )}
        </QueryState>
      )}
    </>
  )
}
