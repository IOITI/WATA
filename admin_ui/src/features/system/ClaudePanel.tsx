import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AIAgentActivity,
  Alert,
  AlertDescription,
  Badge,
  Button,
  formatDateTime,
  formatRelative,
  FormDescription,
  FormItem,
  FormLabel,
  Input,
  Panel,
  QueryState,
  Dialog,
  useToast,
  Table,
  TableHead,
  TableRow,
  TableHeaderCell,
  TableBody,
  TableCell,
} from 'ioiti-ui'
import { useState } from 'react'
import { Link } from 'react-router'

import { BrandMark } from '@/components/BrandMark'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { McpCall, McpState, McpToken } from '@/lib/types'

import { callActivity, callsSummary, endpointState, shareLines, TOKEN_STATE } from './claude'

const CALLS_SHOWN = 15

function Share({ state }: { state: McpState }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-5">
      {shareLines(state).map((line) => (
        <div key={line.label}>
          <dt className="text-muted-foreground">{line.label}</dt>
          <dd className={`mt-0.5 font-medium tabular-nums ${line.full ? 'text-warning' : ''}`}>
            {line.value} {line.of && <span className="font-normal text-muted-foreground">{line.of}</span>}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function Tokens({ tokens, onRevoke }: { tokens: McpToken[]; onRevoke: (token: McpToken) => void }) {
  if (tokens.length === 0) {
    return (
      <p className="py-3 text-sm text-muted-foreground">
        No token yet: make one for the Mac Claude Code runs on, shown once.
      </p>
    )
  }
  return (
    // Positioned: the last column's name, for screen readers alone, stays in the frame that scrolls it.
    <Table density="compact" className="min-w-[34rem]">
      <TableHead>
        <TableRow>
          <TableHeaderCell>Token</TableHeaderCell>
          <TableHeaderCell>State</TableHeaderCell>
          <TableHeaderCell>Expires</TableHeaderCell>
          <TableHeaderCell>Last used</TableHeaderCell>
          <TableHeaderCell>
            <span className="sr-only">Revoke</span>
          </TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {tokens.map((token) => (
          <TableRow key={token.id}>
            <TableCell>
              <span className="font-medium">{token.name}</span>
              <span className="ml-2 font-mono text-muted-foreground">{token.id}</span>
            </TableCell>
            <TableCell>
              <Badge tone={TOKEN_STATE[token.state].tone}>{TOKEN_STATE[token.state].label}</Badge>
            </TableCell>
            <TableCell className="text-muted-foreground">
              {token.state === 'revoked' ? '-' : formatRelative(token.expires_at)}
            </TableCell>
            <TableCell className="text-muted-foreground">
              {token.last_used_at ? formatRelative(token.last_used_at) : 'not since the admin started'}
            </TableCell>
            <TableCell className="text-right">
              {token.state === 'active' && (
                <Button variant="ghost" size="sm" onClick={() => onRevoke(token)}>
                  Revoke
                </Button>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Calls({ calls, timeZone }: { calls: McpCall[]; timeZone: string }) {
  const [all, setAll] = useState(false)
  if (calls.length === 0) {
    return (
      <div>
        <h3 className="text-xs font-semibold">Last calls</h3>
        <p className="py-3 text-sm text-muted-foreground">No call since the admin started.</p>
      </div>
    )
  }
  const shown = all ? calls : calls.slice(0, CALLS_SHOWN)
  return (
    <div className="flex flex-col gap-2">
      <AIAgentActivity
        title="Last calls"
        agentName="since the admin started"
        summary={callsSummary(shown)}
        activities={shown.map((call, index) => callActivity(call, index, timeZone))}
        inputLabel="Arguments, as the log keeps them"
        outputLabel="Detail"
      />
      {calls.length > CALLS_SHOWN && (
        <div>
          <Button variant="ghost" size="sm" onClick={() => setAll(!all)}>
            {all ? `Show the last ${CALLS_SHOWN}` : `Show all ${calls.length}`}
          </Button>
        </div>
      )}
    </div>
  )
}

/** A token made for a machine, shown in its answer once: PostgreSQL keeps its sha256 alone. */
function NewTokenDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('claude-mac')
  const [days, setDays] = useState('90')
  const [copied, setCopied] = useState(false)
  const mutation = useMutation({
    mutationFn: () => api.createMcpToken(name.trim(), Number(days)),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['mcp'] }),
  })
  const close = () => {
    mutation.reset()
    setCopied(false)
    onClose()
  }
  const made = mutation.data
  return (
    <Dialog
      open={open}
      onClose={mutation.isPending ? () => {} : close}
      title={made ? `Token ${made.entry.name}` : 'Make a token for Claude'}
      size="md"
      footer={
        made ? (
          <Button size="sm" onClick={close}>
            I have kept it
          </Button>
        ) : (
          <>
            <Button variant="ghost" size="sm" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button size="sm" pending={mutation.isPending} disabled={!name.trim()} onClick={() => mutation.mutate()}>
              Make it
            </Button>
          </>
        )
      }
    >
      {made ? (
        <div className="space-y-3 text-sm">
          <p className="text-text-secondary">
            Shown this once: keep it in the Mac&apos;s Keychain (README, Claude on the server). Valid until{' '}
            {made.entry.expires_at}.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="break-all rounded-md border border-border bg-surface-raised/60 px-2 py-1 text-xs">{made.token}</code>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void navigator.clipboard?.writeText(made.token).then(() => setCopied(true))}
            >
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
        </div>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (name.trim()) mutation.mutate()
          }}
        >
          <FormItem>
            <FormLabel htmlFor="mcp-token-name">Name</FormLabel>
            <Input id="mcp-token-name" value={name} onChange={(event) => setName(event.target.value)} autoComplete="off" />
            <FormDescription>The machine it is for: lowercase letters, digits and dashes.</FormDescription>
          </FormItem>
          <FormItem>
            <FormLabel htmlFor="mcp-token-days">Days</FormLabel>
            <Input id="mcp-token-days" type="number" min={1} max={365} value={days} onChange={(event) => setDays(event.target.value)} />
            <FormDescription>1 to 365.</FormDescription>
          </FormItem>
          {mutation.error && (
            <Alert tone="danger" role="alert">
              <AlertDescription>{mutation.error.message}</AlertDescription>
            </Alert>
          )}
        </form>
      )}
    </Dialog>
  )
}

/** Claude's research through the admin's MCP endpoint, as the operator watches and stops it. */
export function ClaudePanel() {
  const { timezone, actions_enabled: actionsEnabled } = useSession()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [revoking, setRevoking] = useState<McpToken | null>(null)
  const [making, setMaking] = useState(false)
  const mcpQuery = useQuery({ queryKey: ['mcp'], queryFn: api.mcp, refetchInterval: 15_000 })
  const state = mcpQuery.data

  const pauseMutation = useMutation({
    mutationFn: api.pauseMcp,
    onSuccess: (paused) => {
      void queryClient.invalidateQueries({ queryKey: ['mcp'] })
      toast({
        title: paused.paused ? 'Claude’s endpoint paused' : 'Claude’s endpoint resumed',
        description: paused.paused ? 'Every call is refused until you resume it.' : 'Claude’s calls are answered again.',
        tone: 'success',
      })
    },
  })
  const revokeMutation = useMutation({
    mutationFn: (token: McpToken) => api.revokeMcpToken(token.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mcp'] })
      setRevoking(null)
    },
  })

  const shown = state && endpointState(state)
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <BrandMark brand="claude" />
          Claude
        </span>
      }
      description="Claude Code on your Mac researches strategies here through the admin's MCP endpoint: it reads the recorded data, runs Pine studies and backtests, and keeps its own scripts, experiments and shadow rules. It cannot trade, change the configuration, or take a rule live."
      action={
        state?.enabled && (
          <Button
            variant={state.paused.paused ? 'primary' : 'outline'}
            size="sm"
            pending={pauseMutation.isPending}
            onClick={() => pauseMutation.mutate(!state.paused.paused)}
          >
            {state.paused.paused ? 'Resume' : 'Pause Claude'}
          </Button>
        )
      }
    >
      <QueryState isPending={mcpQuery.isPending} error={mcpQuery.error} hasData={!!state}>
        {state && shown && (
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <Badge tone={shown.tone} dot={shown.tone === 'success'}>
                {shown.label}
              </Badge>
              <span className="text-muted-foreground">{shown.detail}</span>
              {state.paused.changed_at && (
                <span className="text-xs text-muted-foreground">
                  {state.paused.paused ? 'Paused' : 'Resumed'} {formatDateTime(state.paused.changed_at, timezone, false)}
                </span>
              )}
            </div>
            {pauseMutation.error && (
              <Alert tone="danger">
                <AlertDescription>{pauseMutation.error.message}</AlertDescription>
              </Alert>
            )}
            <Share state={state} />
            <p className="text-xs text-muted-foreground">
              Its runs wait behind yours and the paper runs. Its experiments and the lock box are on the{' '}
              <Link to="/backtests/experiments" className="underline underline-offset-2">
                Experiments
              </Link>{' '}
              page.{' '}
              {state.limits.shadow_rules
                ? 'It may keep shadow rules of its own; going live stays yours.'
                : 'Its shadow rules are switched off (admin.mcp.shadow_rules in config.json).'}
            </p>
            <div>
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-xs font-semibold">Tokens</h3>
                <Button variant="outline" size="sm" disabled={!actionsEnabled} onClick={() => setMaking(true)}>
                  Make a token
                </Button>
              </div>
              <Tokens tokens={state.tokens} onRevoke={(token) => {
                revokeMutation.reset()
                setRevoking(token)
              }} />
            </div>
            <Calls calls={state.calls} timeZone={timezone} />
          </div>
        )}
      </QueryState>

      <NewTokenDialog open={making} onClose={() => setMaking(false)} />
      <Dialog
        open={revoking !== null}
        onClose={() => setRevoking(null)}
        title={`Revoke ${revoking?.name ?? ''}?`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRevoking(null)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              size="sm"
              pending={revokeMutation.isPending}
              onClick={() => revoking && revokeMutation.mutate(revoking)}
            >
              Revoke
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-text-secondary">
          The token opens the endpoint no more, within seconds. What Claude made stays. A new one can be made here.
        </p>
        {revokeMutation.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {revokeMutation.error.message}
          </p>
        )}
      </Dialog>
    </Panel>
  )
}
