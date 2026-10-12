import { formatMoney, formatPercent } from '@/lib/format'
import { actionLabel } from '@/lib/labels'
import type { LiveEvent } from '@/lib/types'

/** The event types the server sends on /api/events (src/admin/events.py). */
export const LIVE_EVENT_TYPES = [
  'position_opened',
  'position_closed',
  'signal_decided',
  'entries_paused',
  'entries_resumed',
  'service_status',
  'saxo_authorisation_required',
  'saxo_authorised',
] as const

export interface EventNotice {
  title: string
  body?: string
  variant: 'default' | 'success' | 'warning' | 'error' | 'info'
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined
}

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const OUTCOME_TEXT: Record<string, string> = {
  executed: 'executed',
  closed: 'positions closed',
  refused: 'refused',
  failed: 'failed',
  ignored: 'nothing to do',
}

const SERVICE_STATE_TEXT: Record<string, string> = {
  ok: 'is back up',
  warning: 'is degraded',
  down: 'is down',
  unknown: 'can no longer be observed',
  disabled: 'is disabled',
}

/** What to tell the operator about an event, or null when the page refreshing is enough. */
export function describeEvent(event: LiveEvent): EventNotice | null {
  switch (event.type) {
    case 'position_opened':
      return {
        title: 'Position opened',
        body: [actionLabel(text(event.action)), text(event.instrument_name)].filter(Boolean).join(' · '),
        variant: 'info',
      }
    case 'position_closed': {
      const profit = number(event.profit_loss)
      const performance = number(event.performance_percent)
      const result =
        profit === null ? 'result not recorded yet' : `${formatMoney(profit, 'EUR', true)} (${formatPercent(performance, true)})`
      return {
        title: 'Position closed',
        body: [text(event.instrument_name), result].filter(Boolean).join(' · '),
        variant: profit === null ? 'info' : profit >= 0 ? 'success' : 'warning',
      }
    }
    case 'signal_decided': {
      const outcome = text(event.outcome) ?? ''
      return {
        title: `${actionLabel(text(event.action))} signal: ${OUTCOME_TEXT[outcome] ?? outcome}`,
        body: text(event.reason),
        variant: outcome === 'failed' ? 'error' : outcome === 'refused' ? 'warning' : 'info',
      }
    }
    case 'service_status': {
      const status = text(event.status) ?? 'unknown'
      return {
        title: `${text(event.name) ?? 'A service'} ${SERVICE_STATE_TEXT[status] ?? status}`,
        body: text(event.detail),
        variant: status === 'down' ? 'error' : status === 'ok' ? 'success' : 'warning',
      }
    }
    case 'saxo_authorisation_required':
      return {
        title: 'Saxo authorisation required',
        body: 'The refresh token expired: hand over a new code on the System page.',
        variant: 'error',
      }
    case 'saxo_authorised':
      return { title: 'Saxo is authorised again', variant: 'success' }
    default:
      // Pauses are shown by the dashboard itself, to whoever caused them and to everyone else.
      return null
  }
}

/** The cached data an event makes out of date, as query-key prefixes. */
export function staleQueries(event: LiveEvent): string[] {
  switch (event.type) {
    case 'position_opened':
      return ['overview', 'positions', 'orders']
    case 'position_closed':
      return ['overview', 'positions', 'position', 'orders', 'performance', 'exits', 'report']
    case 'signal_decided':
      return ['journal', 'signals']
    case 'entries_paused':
    case 'entries_resumed':
      return ['overview']
    case 'service_status':
      return ['health']
    case 'saxo_authorisation_required':
    case 'saxo_authorised':
      // The health summary carries the Saxo connection too (top bar, dashboard).
      return ['saxo', 'health']
    default:
      return []
  }
}
