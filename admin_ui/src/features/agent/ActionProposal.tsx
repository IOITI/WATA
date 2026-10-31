// An action on live trading the agent proposed (a pause or a resume of new entries, a position closed, services
// restarted, a rule taken live or out of live), under its answer: what it does and what was seen when proposed,
// then the page's own confirmation of it (the dashboard's pause, the Positions page's close, System's restart,
// Signal rules' switch), sent with the proposal so that the audit trail says so. Nothing is done without that
// confirmation, and nothing after its 30 minutes: the card then leads to the page where it is done by hand.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Button, ButtonLink, formatDateTime, useNow } from 'ioiti-ui'
import { Zap } from 'lucide-react'
import { useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { PauseDialog } from '@/features/dashboard/PauseDialog'
import { ClosePositionDialog } from '@/features/positions/ClosePositionDialog'
import { RestartDialog } from '@/features/system/RestartViews'
import { useServices } from '@/features/system/useServices'
import { liveReadiness } from '@/features/ta/signals/readiness'
import { RuleModeDialog } from '@/features/ta/signals/RuleModeDialog'
import { api } from '@/lib/api'
import type { AgentAction, AgentProposal } from '@/lib/types'

import { AgentVerdict } from './AgentVerdict'
import { actionFresh, actionPage, actionSaid } from './model'
import { conversationKey } from './useConversation'

function Seen({ action }: { action: AgentAction }) {
  const { timezone } = useSession()
  if (action.kind === 'close_position') {
    return (
      <p className="text-sm text-text-secondary">
        Open when proposed: {action.action ?? 'a position'} {action.instrument_name ?? action.position_id}
        {action.execution_time_open && <>, bought {formatDateTime(action.execution_time_open, timezone, false)}</>}.
      </p>
    )
  }
  if (action.kind === 'restart') {
    return (
      <p className="text-sm text-text-secondary">
        {(action.services ?? []).map((service) => service.name).join(', ')}: one after the other, each once the one before
        is at work again, the admin last; held back as System holds a restart.
      </p>
    )
  }
  if (action.rule) {
    return (
      <p className="text-sm text-text-secondary">
        {action.rule.name}, for {action.rule.indice}: {action.rule.mode} when proposed.
      </p>
    )
  }
  return null
}

function RestartOfProposal({ action, proposal, onClose, onDone, onRefused }: {
  action: AgentAction
  proposal: string
  onClose: () => void
  onDone: () => void
  onRefused: (message: string) => void
}) {
  const services = useServices()
  if (!services.data) return null
  return (
    <RestartDialog state={services.data} ids={(action.services ?? []).map((service) => service.id)} onClose={onClose}
      onStarted={onDone} proposal={proposal} onRefused={onRefused} />
  )
}

function RuleModeOfProposal({ action, proposal, onClose, onDone, onRefused }: {
  action: AgentAction
  proposal: string
  onClose: () => void
  onDone: () => void
  onRefused: (message: string) => void
}) {
  const { timezone, backtests_enabled: backtests } = useSession()
  const now = useNow(60_000)
  const rules = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules })
  const engine = useQuery({ queryKey: ['ta-engine'], queryFn: api.taEngine })
  const paper = useQuery({ queryKey: ['backtest-paper'], queryFn: api.backtestPaper, enabled: backtests, staleTime: 60_000 })
  const rule = rules.data?.rules.find((entry) => entry.id === action.rule?.id)
  if (!rules.data || !rule) return null
  const mode = action.kind === 'rule_live' ? 'live' : 'shadow'
  const readiness = mode === 'live'
    ? liveReadiness(rule, engine.data?.rules?.[rule.id], paper.data?.rules[rule.id], now, timezone)
    : []
  return (
    <RuleModeDialog change={{ rule, mode }} revision={rules.data.revision} readiness={readiness} onClose={onClose}
      onDone={onDone} onRefused={onRefused} proposal={proposal}
      verdict={<AgentVerdict about={`rule:${rule.id}`} changedAt={rule.run_changed_at} />} />
  )
}

export function ActionProposal({ conversationId, proposal }: { conversationId: string; proposal: AgentProposal }) {
  const { timezone, actions_enabled: actionsEnabled } = useSession()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  // What the server answered the last time it refused it: the proposal stays open, and says so.
  const [refused, setRefused] = useState<string | null>(null)
  // Its freshness, looked at again every half minute: past it, the card leads to the page instead.
  const now = useNow(30_000)
  const action = proposal.action!
  const ref = `${conversationId}/${proposal.id}`
  const fresh = actionFresh(proposal, now)
  const page = actionPage(action)
  const close = () => setOpen(false)
  const done = () => {
    setRefused(null)
    void queryClient.invalidateQueries({ queryKey: conversationKey(conversationId) })
  }

  return (
    <div className="space-y-2">
      <Seen action={action} />
      {proposal.status === 'proposed' && (fresh && actionsEnabled ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant={action.kind === 'close_position' ? 'destructive' : 'primary'} leftIcon={<Zap className="size-3.5" />}
            onClick={() => setOpen(true)}>
            {actionSaid(action)}…
          </Button>
          {proposal.fresh_until && (
            <span className="text-xs text-muted-foreground">
              From here until {formatDateTime(proposal.fresh_until, timezone, false)}, through the page&apos;s own confirmation.
            </span>
          )}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          {actionsEnabled
            ? 'Proposed over 30 minutes ago: the situation may have changed. '
            : 'Operator actions are disabled in the configuration (admin.actions_enabled). '}
          <ButtonLink to={page.to} size="sm" variant="ghost">Go to {page.label}</ButtonLink>
        </p>
      ))}
      {refused && proposal.status === 'proposed' && (
        <Alert tone="danger">
          <AlertDescription>Refused when you tried it: {refused}</AlertDescription>
        </Alert>
      )}
      {open && (action.kind === 'pause_entries' || action.kind === 'resume_entries') && (
        <PauseDialog paused={action.kind === 'resume_entries'} open onClose={close} proposal={ref} onDone={done}
          onRefused={setRefused} />
      )}
      {open && action.kind === 'close_position' && (
        <ClosePositionDialog position={{ position_id: action.position_id ?? '', instrument_name: action.instrument_name ?? null }}
          open onClose={close} proposal={ref} onDone={done} onRefused={setRefused} />
      )}
      {open && action.kind === 'restart' && (
        <RestartOfProposal action={action} proposal={ref} onClose={close} onDone={done} onRefused={setRefused} />
      )}
      {open && (action.kind === 'rule_live' || action.kind === 'rule_out_of_live') && (
        <RuleModeOfProposal action={action} proposal={ref} onClose={close} onDone={done} onRefused={setRefused} />
      )}
    </div>
  )
}
