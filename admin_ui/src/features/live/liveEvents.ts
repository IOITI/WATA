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
  'backtest_finished',
  'prices_fetch_finished',
  'calibration_finished',
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
    case 'backtest_finished': {
      const state = text(event.state) ?? ''
      // Stopped by the operator: they know.
      if (state === 'cancelled') return null
      return {
        title: state === 'done' ? 'Backtest finished' : 'Backtest failed',
        body: text(event.note),
        variant: state === 'done' ? 'success' : 'error',
      }
    }
    case 'prices_fetch_finished': {
      const state = text(event.state) ?? ''
      // Stopped by the operator, or one of the downloads the service makes by itself: no news.
      if (state === 'stopped' || event.automatic === true) return null
      // Another Dukascopy instrument than the history backtests read is named.
      const label = text(event.label)
      const what = label && label !== 'us100' ? `Prices of ${label}` : 'Prices'
      return {
        title: state === 'done' ? `${what} downloaded` : `The download of ${what.toLowerCase()} ended`,
        body:
          state === 'incomplete'
            ? 'Some days are still missing: run the same period again.'
            : state === 'failed'
              ? 'It failed.'
              : undefined,
        variant: state === 'done' ? 'success' : 'warning',
      }
    }
    case 'calibration_finished':
      return text(event.state) === 'done'
        ? { title: 'Turbo measured on the real trades', variant: 'success' }
        : { title: 'The measure of the turbo failed', variant: 'error' }
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
      // The price recorder is one of the services, and the Saxo provider is what it does.
      return ['health', 'data-providers', 'data-provider']
    case 'saxo_authorisation_required':
    case 'saxo_authorised':
      // The health summary carries the Saxo connection too (top bar, dashboard).
      return ['saxo', 'health']
    case 'backtest_finished':
      return ['backtests', 'backtest']
    case 'prices_fetch_finished':
      // Days fetched are days of an asset. Never a chart's own queries: every open chart would be read again.
      return ['backtest-prices', 'data-assets', 'data-asset', 'data-providers', 'data-provider']
    case 'calibration_finished':
      // The turbo a new backtest is offered comes from it.
      return ['backtest-calibration', 'backtest-options']
    default:
      return []
  }
}
