// A script's page, its "As a rule" tab: whether a version can become a signal rule as it is
// (its rule check, src/admin/rule_checks.py), its run test asked as the rule that runs it runs
// it, a live one first (else on minutes of Capital.com's US100), each finding leading to its line in the
// editor, the events it only draws written in as alerts, unsaved, and the AI agent asked to
// make it ready. What the editor holds unsaved is read in the page, as the editor marks it.

import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, AlertTitle, ButtonLink, QueryState } from 'ioiti-ui'
import { useSearchParams } from 'react-router'

import { NEW, withAgent } from '@/features/agent/model'
import { useAgentState } from '@/features/agent/useConversation'
import type { RuleCheck } from '@/features/ta/engine/ruleCheck'
import { RuleCheckPanel } from '@/features/ta/ruleCheck/RuleCheckPanel'
import { draftReport, testingOf, VERDICT_LABEL, verdictSaid } from '@/features/ta/ruleCheck/ruleCheckModel'
import { useAskRuleTest, useScriptRuleCheck } from '@/features/ta/ruleCheck/useRuleCheck'
import { barsLabel } from '@/features/ta/signals/ruleModel'
import { api } from '@/lib/api'
import type { AlertCandidate, RuleFinding, TaRuleMode, TaScriptSummary } from '@/lib/types'

import { editorLineHref } from './scriptPageModel'

// The rule a version is tested as, of those that pin it: the one going live reads first.
const MODE_ORDER: Record<TaRuleMode, number> = { live: 0, shadow: 1, off: 2 }

export function ScriptRuleTab({
  script,
  version,
  dirty,
  draftCheck,
  onAddAlerts,
}: {
  script: TaScriptSummary
  version: number
  /** Whether the editor holds changes not saved. */
  dirty: boolean
  /** What the editor's text says, read in the page. */
  draftCheck: RuleCheck | null
  onAddAlerts: (chosen: AlertCandidate[]) => void
}) {
  const [searchParams] = useSearchParams()
  const query = useScriptRuleCheck(script.id, version)
  const rules = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules })
  const agentOn = !!useAgentState().data?.enabled
  // A rule that runs this version: tested as it runs it (its asset, side, bars, inputs and history), which its
  // going live then counts; else on minutes.
  const pinning = (rules.data?.rules ?? [])
    .filter((rule) => rule.script.id === script.id && rule.script.version === version)
    .sort((a, b) => MODE_ORDER[a.mode] - MODE_ORDER[b.mode])[0] ?? null
  const ask = useAskRuleTest((body) => (pinning ? api.testRuleAsItRuns(pinning.id) : api.testScriptAsRule(script.id, version, body)))
  const report = query.data?.report ?? null
  const state = query.data?.state
  const testing = testingOf(state, { script: { id: script.id, version } }) ?? (pinning ? testingOf(state, { rule: pinning.id }) : null)
  const newest = version === script.version
  // A line of this version is the editor's only when it shows this version, unchanged.
  const lineHref = newest && !dirty
    ? (finding: RuleFinding) => (finding.places[0] && finding.places[0].file === null ? editorLineHref(finding.places[0].line) : null)
    : undefined
  // The alerts chosen, written in the text the editor holds: its own lines when it was changed since.
  const add = (chosen: AlertCandidate[]) => {
    if (!dirty || !draftCheck) return onAddAlerts(chosen)
    const titles = new Set(chosen.map((candidate) => candidate.title))
    onAddAlerts(draftCheck.candidates.filter((candidate) => titles.has(candidate.title)))
  }

  return (
    <div className="flex flex-col gap-4">
      {dirty && newest && draftCheck && (
        <Alert tone="info">
          <AlertTitle>Your changes, not saved: {VERDICT_LABEL[draftCheck.verdict]}</AlertTitle>
          <AlertDescription>
            {verdictSaid(draftReport(draftCheck))} What follows is version {version} as saved: save them to test them on recorded bars.
          </AlertDescription>
        </Alert>
      )}
      <QueryState isPending={query.isPending} error={query.error} hasData={!!report}>
        {report && (
          <RuleCheckPanel
            report={report}
            testing={testing}
            onTest={() => ask.mutate({ bars: '1' })}
            testLabel={pinning ? `Test as ${pinning.name} runs it (${barsLabel(pinning.bars)})` : 'Test on recorded bars (1 min)'}
            testRefusal={state && !state.available ? 'The run test is a Pine study, which this admin does not run' : null}
            testPending={ask.isPending}
            lineHref={lineHref}
            onAddAlerts={newest ? add : undefined}
            aside={
              agentOn && report.verdict !== 'ready' ? (
                <div>
                  <ButtonLink to={withAgent(searchParams, NEW)} variant="outline" size="sm">
                    Ask the AI agent to make it ready
                  </ButtonLink>
                </div>
              ) : null
            }
          />
        )}
      </QueryState>
    </div>
  )
}
