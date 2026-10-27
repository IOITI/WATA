/**
 * What the System page says of Claude's research through the admin's MCP
 * endpoint: whether the endpoint can be reached, what Claude's work holds of
 * the server against its limits, and its calls in a line each.
 */

import { formatDateTime, formatNumber } from 'ioiti-ui'
import type { ActivityStatus, AgentActivityItemData, BadgeTone } from 'ioiti-ui'

import type { McpCall, McpCallOutcome, McpState, McpTokenState } from '@/lib/types'

/** Who Claude's work is written as, whichever of its tokens asked. */
export const CLAUDE = 'claude'

export function madeByClaude(row: { made_by?: string | null }): boolean {
  return row.made_by === CLAUDE
}

export interface EndpointState {
  tone: BadgeTone
  label: string
  detail: string
}

export function endpointState(state: McpState): EndpointState {
  if (!state.enabled) {
    return {
      tone: 'neutral',
      label: 'Off',
      detail: 'Switched off in the configuration (admin.mcp_enabled): the admin answers no call of Claude’s.',
    }
  }
  if (state.paused.paused) {
    return { tone: 'warning', label: 'Paused', detail: 'Every call of Claude’s is refused until the endpoint is resumed.' }
  }
  if (!state.tokens_readable) {
    return {
      tone: 'danger',
      label: 'No token',
      detail: 'The tokens file cannot be read, so no token opens the endpoint: move it away on the server and make a token again.',
    }
  }
  const active = state.tokens.filter((token) => token.state === 'active').length
  if (active === 0) {
    return { tone: 'neutral', label: 'No token', detail: 'Open, but no valid token: make one on the server (watamcptoken create <name>).' }
  }
  return { tone: 'success', label: 'Open', detail: `${active} valid token${active === 1 ? '' : 's'}, over WireGuard on the admin port.` }
}

export const TOKEN_STATE: Record<McpTokenState, { tone: BadgeTone; label: string }> = {
  active: { tone: 'success', label: 'Valid' },
  expired: { tone: 'neutral', label: 'Expired' },
  revoked: { tone: 'neutral', label: 'Revoked' },
}

export const OUTCOME: Record<McpCallOutcome, { tone: BadgeTone; label: string }> = {
  ok: { tone: 'success', label: 'Done' },
  refused: { tone: 'warning', label: 'Refused' },
  failed: { tone: 'danger', label: 'Failed' },
  cancelled: { tone: 'neutral', label: 'Given up' },
}

export interface ShareLine {
  label: string
  value: string
  /** Its limit, said with it. */
  of: string | null
  /** At or over its limit: Claude is refused more of it until some is done. */
  full: boolean
}

function count(value: number | null, limit: number | null, label: string, unit = ''): ShareLine {
  return {
    label,
    value: value === null ? '-' : `${value}${unit}`,
    of: limit === null ? null : `of ${limit}${unit}`,
    full: value !== null && limit !== null && value >= limit,
  }
}

/** What Claude's work holds of the server, each beside the most it may. */
export function shareLines(state: McpState): ShareLine[] {
  const { share, limits } = state
  const minutes = share.study_seconds_last_hour === null ? null : Math.round(share.study_seconds_last_hour / 60)
  const rules = count(share.rules, limits.max_shadow_rules, 'Shadow rules')
  return [
    count(share.waiting_runs, limits.max_waiting_runs, 'Runs waiting'),
    count(share.runs_today, limits.runs_per_day, 'Runs today (UTC)'),
    count(minutes, limits.study_minutes_per_hour, 'Pine studies, last hour', ' min'),
    count(share.scripts, limits.max_scripts, 'Scripts'),
    limits.shadow_rules ? rules : { ...rules, of: 'switched off', full: false },
  ]
}

const SHOWN_ARGUMENT = 40

function given(value: unknown): boolean {
  if (value === null || value === undefined || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  return typeof value !== 'object' || Object.keys(value).length > 0
}

/** A call's arguments in a line, those left empty left out: a long one (a Pine source) as its length, as the log keeps it. */
export function callArguments(call: McpCall): string {
  return Object.entries(call.arguments)
    .filter(([, value]) => given(value))
    .map(([name, value]) => {
      if (value && typeof value === 'object' && 'sha256' in value && 'chars' in value) {
        return `${name}: ${(value as { chars: number }).chars} characters`
      }
      const text = typeof value === 'string' ? value : JSON.stringify(value)
      return `${name}: ${text.length > SHOWN_ARGUMENT ? `${text.slice(0, SHOWN_ARGUMENT)}…` : text}`
    })
    .join(', ')
}

const CALL_STATUS: Record<McpCallOutcome, ActivityStatus> = {
  ok: 'success',
  refused: 'error',
  failed: 'error',
  cancelled: 'cancelled',
}

/**
 * A call as a step of Claude's trace: the tool, what it was asked in a line (or why it was refused), its outcome in
 * words and how long it took; opened, its arguments as the log keeps them and the token that made it.
 */
export function callActivity(call: McpCall, index: number, timeZone: string): AgentActivityItemData {
  const asked = Object.keys(call.arguments).length > 0
  return {
    id: `${call.at}-${call.tool}-${index}`,
    type: 'tool_execution',
    title: call.tool,
    description: call.detail ?? (callArguments(call) || undefined),
    status: CALL_STATUS[call.outcome] ?? 'pending',
    statusLabel: OUTCOME[call.outcome]?.label ?? call.outcome,
    duration: `${formatNumber(call.milliseconds)} ms`,
    timestamp: formatDateTime(call.at, timeZone),
    details: asked || call.detail ? { input: asked ? call.arguments : undefined, output: call.detail } : undefined,
    metadata: call.caller ? { token: call.caller } : undefined,
  }
}

/** What the calls shown add up to: "15 calls: 13 done, 2 refused". */
export function callsSummary(calls: McpCall[]): string {
  const counts = new Map<string, number>()
  for (const call of calls) {
    const label = (OUTCOME[call.outcome]?.label ?? call.outcome).toLowerCase()
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  const parts = [...counts].map(([label, count]) => `${count} ${label}`)
  return `${calls.length} call${calls.length === 1 ? '' : 's'}: ${parts.join(', ')}`
}

