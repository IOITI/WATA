/**
 * What the System page says of Claude's research through the admin's MCP
 * endpoint: whether the endpoint can be reached, what Claude's work holds of
 * the server against its limits, and its calls in a line each.
 */

import type { BadgeTone } from '@/components/ui/badge'
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
