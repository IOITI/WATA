import { formatMoney, formatPercent } from 'ioiti-ui'

import { orderLink, positionLink } from '@/features/positions/orderLife'
import { actionLabel } from '@/lib/labels'
import { planOutcome, planSucceeded } from '@/features/system/restarts'
import type { LiveEvent, RestartPlan } from '@/lib/types'

/** The event types the server sends on /api/events (src/admin/events.py). */
export const LIVE_EVENT_TYPES = [
  'position_opened',
  'position_closed',
  'order_refused',
  'signal_decided',
  'entries_paused',
  'entries_resumed',
  'service_status',
  'saxo_authorisation_required',
  'saxo_authorised',
  'backtest_finished',
  'prices_fetch_finished',
  'restart_plan',
  'release',
  'agent_finished',
  'agent_question',
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

// How a request of the AI agent ended (src/admin/agent/loop.py).
const AGENT_OUTCOME_TEXT: Record<string, string> = {
  done: 'The AI agent answered',
  limit: 'The AI agent reached a limit',
  cut: "The AI agent's answer was cut",
  failed: 'The AI agent failed',
  declined: 'The AI agent declined',
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
    case 'order_refused': {
      // A sell Saxo did not take (src/admin/events.py): announced at most once a minute per position.
      const count = number(event.count) ?? 1
      const message = text(event.error_message)
      const status = number(event.http_status)
      const labels = [text(event.error_code), status !== null ? `HTTP ${status}` : undefined].filter(Boolean).join(', ')
      const answer = message ? `“${message}”${labels ? ` (${labels})` : ''}` : undefined
      const position = text(event.position_id)
      const what = text(event.outcome) === 'no_answer' ? 'Saxo did not answer a sell' : 'Saxo refused a sell'
      return {
        title: count > 1 ? `${what} (×${count})` : what,
        body: [text(event.instrument_name), position && `position ${position}`, answer].filter(Boolean).join(' · '),
        variant: 'error',
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
        body: text(event.name),
        variant: state === 'done' ? 'success' : 'error',
      }
    }
    case 'agent_finished': {
      const outcome = text(event.outcome) ?? ''
      const task = text(event.task)
      // A research task going on to its next request: said once it ends, not at each request.
      if (task === 'working') return null
      // Stopped by the operator: they know.
      if (outcome === 'stopped') return null
      if (task === 'done' || task === 'ended') {
        return {
          title: task === 'done' ? 'The research task is done' : 'The research task ended',
          body: [text(event.title), task === 'ended' ? text(event.task_detail) : undefined].filter(Boolean).join(' · '),
          variant: task === 'done' ? 'success' : 'warning',
        }
      }
      const cost = number(event.cost_usd)
      const title = AGENT_OUTCOME_TEXT[outcome] ?? 'The AI agent stopped'
      return {
        title,
        body: [text(event.title), outcome === 'done' ? undefined : text(event.detail),
          cost !== null ? `$${cost.toFixed(2)}` : undefined].filter(Boolean).join(' · '),
        variant: outcome === 'done' ? 'success' : outcome === 'failed' || outcome === 'declined' ? 'error' : 'warning',
      }
    }
    case 'agent_question':
      // A research task waits for the operator's answer.
      return {
        title: 'The research task asks you',
        body: [text(event.title), text(event.question)].filter(Boolean).join(' · '),
        variant: 'info',
      }
    case 'prices_fetch_finished': {
      const state = text(event.state) ?? ''
      // Stopped by the operator, or one of the downloads the service makes by itself: no news.
      if (state === 'stopped' || event.automatic === true) return null
      // Another Dukascopy instrument than the history kept up to date is named.
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
    case 'restart_plan': {
      // Said once, when it ends: the page that asked for it shows it as it goes.
      const plan = event.plan as RestartPlan | undefined
      if (!plan?.done) return null
      return {
        title: planSucceeded(plan) ? 'Restart done' : 'Restart stopped',
        body: planOutcome(plan, plan.names ?? {}),
        variant: planSucceeded(plan) ? 'success' : 'warning',
      }
    }
    default:
      // Pauses are shown by the dashboard itself, to whoever caused them and to everyone else.
      return null
  }
}

/** Where an event is dealt with, or read further: the page its entry in the bell leads to. */
export function eventLink(event: LiveEvent): string | null {
  switch (event.type) {
    case 'position_opened':
    case 'position_closed': {
      const position = text(event.position_id)
      return position ? positionLink(position) : '/positions'
    }
    case 'order_refused': {
      // The Orders page finds the sells Saxo refused by their position.
      const position = text(event.position_id)
      return position ? orderLink(position) : '/orders'
    }
    case 'signal_decided':
      return '/signals'
    case 'entries_paused':
    case 'entries_resumed':
      return '/'
    case 'service_status':
    case 'saxo_authorisation_required':
    case 'saxo_authorised':
      return '/system'
    case 'backtest_finished': {
      const run = text(event.run_id)
      return run ? `/backtests/${encodeURIComponent(run)}` : '/backtests'
    }
    case 'prices_fetch_finished':
      return '/data/providers/dukascopy'
    case 'restart_plan':
      return '/system'
    case 'release':
      return '/system#release'
    case 'agent_finished':
    case 'agent_question': {
      const conversation = text(event.conversation_id)
      return conversation ? `/agent/${encodeURIComponent(conversation)}` : '/agent'
    }
    default:
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
    case 'order_refused':
      return ['overview', 'positions', 'position', 'orders']
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
      // A paper day or report is a run too: the pages that list them read them again.
      return ['backtests', 'backtest', 'backtest-paper', 'live-paper']
    case 'agent_finished':
      // Its runs, scripts and proposals are read again with it.
      return ['agent', 'agent-conversations', 'agent-conversation', 'agent-artefacts', 'backtests', 'ta-script']
    case 'agent_question':
      return ['agent-conversations', 'agent-conversation']
    case 'prices_fetch_finished':
      // Days fetched are days of an asset. Never a chart's own queries: every open chart would be read again.
      return ['history-coverage', 'data-assets', 'data-asset', 'data-providers', 'data-provider']
    case 'restart_plan':
      // A service started again runs with the file as it is: its changes are no longer waiting.
      return ['services', 'session', 'health', 'config', 'data-provider']
    case 'release':
      // watadeploy wrote its status: what runs where, what waits, an apply as it goes. Said on Telegram once it
      // ends (src/admin/release.py), so no toast: the System page shows it.
      return ['release', 'session', 'services', 'health']
    default:
      return []
  }
}
