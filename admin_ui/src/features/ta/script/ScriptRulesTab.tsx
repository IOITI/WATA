// The signal rules that run a script (its page's Rules tab): each with its mode, the version it pins
// against the newest, what it runs on and its days on paper, leading to its own page. A library's are
// the rules whose pins hold it, beside the scripts that import it.

import { useQuery } from '@tanstack/react-query'
import { Badge, Panel, QueryState } from 'ioiti-ui'
import { Link } from 'react-router'

import { ClaudeBadge } from '@/components/status'
import { paperReportHref } from '@/features/backtests/paperModel'
import { usePaper } from '@/features/backtests/usePaper'
import { useSession } from '@/features/auth/auth-context'
import { madeByClaude } from '@/features/system/claude'
import { paperLine } from '@/features/ta/signals/paper'
import { barsLabel, MODE_LABEL, MODE_TONE, rulePath } from '@/features/ta/signals/ruleModel'
import { importersOf } from '@/features/ta/templates'
import { api } from '@/lib/api'
import type { TaRule, TaScriptSummary } from '@/lib/types'

import { modeCounts, modesSaid, pinnedSaid, rulesRunning } from './scriptPageModel'

function RuleRow({ rule, script, paper }: { rule: TaRule; script: TaScriptSummary; paper: ReturnType<typeof paperLine> }) {
  return (
    <li className="flex flex-col gap-1 border-b border-border/60 py-3 last:border-b-0 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2">
          <Link to={rulePath(rule.id)} className="font-medium underline-offset-4 hover:underline">
            {rule.name}
          </Link>
          <Badge tone={MODE_TONE[rule.mode]} dot={rule.mode === 'live'} pulse={rule.mode === 'live'}>
            {MODE_LABEL[rule.mode]}
          </Badge>
          {madeByClaude(rule) && <ClaudeBadge />}
        </p>
        <p className="text-xs text-muted-foreground">
          {pinnedSaid(rule, script)} · {rule.asset.key} · {barsLabel(rule.bars)} · signals for {rule.indice}
        </p>
      </div>
      {paper && (
        <div className="shrink-0 text-xs sm:max-w-72 sm:text-right">
          {paper.report ? (
            <Link className="font-medium underline-offset-2 hover:underline" to={paperReportHref(rule.id)}>
              {paper.report}
            </Link>
          ) : (
            <span className="font-medium">{paper.total}</span>
          )}
          {paper.last && <p className="text-muted-foreground">{paper.last}</p>}
        </div>
      )}
    </li>
  )
}

export function ScriptRulesTab({ script }: { script: TaScriptSummary }) {
  const { backtests_enabled: backtests } = useSession()
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules })
  const paperQuery = usePaper(backtests)
  const scriptsQuery = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts, enabled: script.kind === 'library' })
  const rules = rulesRunning(script.id, rulesQuery.data?.rules ?? [])
  const users = script.kind === 'library' ? (importersOf(scriptsQuery.data?.items ?? []).get(script.library_name ?? '') ?? []) : []

  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="Signal rules"
        description={
          script.kind === 'library'
            ? 'The rules whose script imports this library: each pins the version it runs.'
            : 'The rules that run this script, each at the version it pins: its own page says how ready it is to go live.'
        }
      >
        <QueryState isPending={rulesQuery.isPending} error={rulesQuery.error} hasData={Boolean(rulesQuery.data)}>
          {rules.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {script.kind === 'library' ? 'No rule runs a script that imports it.' : 'No rule runs it yet.'}
            </p>
          ) : (
            <>
              <p className="text-sm text-text-secondary">{modesSaid(modeCounts(rules))}</p>
              <ul aria-label="Rules running it">
                {rules.map((rule) => (
                  <RuleRow key={rule.id} rule={rule} script={script} paper={paperLine(paperQuery.data?.rules[rule.id], rule.mode)} />
                ))}
              </ul>
            </>
          )}
        </QueryState>
      </Panel>
      {script.kind === 'library' && (
        <Panel title="Used by" description="The scripts that import it.">
          {users.length === 0 ? (
            <p className="text-sm text-muted-foreground">No script imports it yet.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm">
              {users.map((user) => (
                <li key={user.id}>
                  <Link to={`/ta/indicators/${user.id}`} className="underline-offset-4 hover:underline">
                    {user.name}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}
    </div>
  )
}
