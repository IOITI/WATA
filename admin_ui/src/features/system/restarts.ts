// What the pages say of WATA's containers and their restarts (src/admin/routers/services.py,
// src/admin/service_restart.py): pure, so that what can be said wrong is tested.

import type { BadgeTone } from '@/components/ui/badge'
import { formatDateTime } from '@/lib/format'
import type { RestartPlan, RestartStepState, ServicesState, Session, WataService } from '@/lib/types'

export const STEP_LABEL: Record<RestartStepState, string> = {
  waiting: 'Waiting',
  restarting: 'Restarting',
  starting: 'Starting',
  ready: 'At work again',
  failed: 'Failed',
  held: 'Held back',
  skipped: 'Not restarted',
}

export const STEP_TONE: Record<RestartStepState, BadgeTone> = {
  waiting: 'neutral',
  restarting: 'warning',
  starting: 'warning',
  ready: 'success',
  failed: 'danger',
  held: 'warning',
  skipped: 'neutral',
}

/** Whether a plan of restarts is still going. */
export function planRunning(plan: RestartPlan | null | undefined): boolean {
  return !!plan && !plan.done
}

function counted(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

function listed(names: string[]): string {
  return names.length <= 1 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/** What a plan came to, in a sentence, its services named. */
export function planOutcome(plan: RestartPlan, names: Record<string, string>): string {
  const name = (service: string) => names[service] ?? service
  const of = (state: RestartStepState) => plan.steps.filter((step) => step.state === state)
  const ready = of('ready').map((step) => name(step.service))
  const parts: string[] = []
  if (ready.length) parts.push(`${listed(ready)} ${ready.length === 1 ? 'is' : 'are'} at work again`)
  for (const step of [...of('failed'), ...of('held')]) {
    parts.push(`${name(step.service)}: ${step.detail ?? STEP_LABEL[step.state].toLowerCase()}`)
  }
  const skipped = of('skipped').filter((step) => step.detail?.startsWith('Not restarted'))
  if (skipped.length) parts.push(`not restarted: ${listed(skipped.map((step) => name(step.service)))}`)
  const absent = of('skipped').filter((step) => !step.detail?.startsWith('Not restarted'))
  if (absent.length) parts.push(`${listed(absent.map((step) => name(step.service)))}: not created, its profile off`)
  if (plan.steps.some((step) => step.service === 'admin' && step.state === 'restarting')) {
    parts.push('the admin restarts: sign in again in a few seconds')
  }
  const said = parts.join('; ')
  return said ? said.charAt(0).toUpperCase() + said.slice(1) + '.' : 'Nothing was restarted.'
}

/** Whether a plan ended as asked: every service at work again (the admin, restarting, ends it). */
export function planSucceeded(plan: RestartPlan): boolean {
  return plan.steps.every(
    (step) =>
      step.state === 'ready' ||
      (step.service === 'admin' && step.state === 'restarting') ||
      (step.state === 'skipped' && !step.detail?.startsWith('Not restarted')),
  )
}

/** Whether a service runs without a change saved since it started. */
export function hasPending(service: WataService): boolean {
  return service.pending.paths.length > 0 || service.pending.outside
}

/** The services that run without a change saved since they started, in the order a plan restarts them. */
export function withPendingChanges(state: ServicesState): WataService[] {
  return state.services.filter((service) => service.state !== 'absent' && hasPending(service))
}

/** How a service's changes not applied yet are said; null when there are none. */
export function pendingSaid(service: WataService): string | null {
  const parts: string[] = []
  if (service.pending.paths.length) parts.push(`${counted(service.pending.paths.length, 'saved change')} not applied`)
  if (service.pending.outside) parts.push('the file changed on the server since it started')
  return parts.length ? parts.join('; ') : null
}

/** What its container is doing, as Docker says. */
export function containerSaid(service: WataService, timeZone: string): string {
  if (service.state === null) return 'Unknown'
  if (service.state === 'absent') return 'Not created: its compose profile is off'
  if (service.restarting) return 'Restarting'
  if (service.running) return `Running since ${formatDateTime(service.started_at, timeZone)}`
  const code = service.exit_code !== null && service.exit_code !== undefined ? `, exit code ${service.exit_code}` : ''
  return `${service.state.charAt(0).toUpperCase()}${service.state.slice(1)}${code}`
}

/** What holds back a restart of ``ids``, service by service. */
export function heldBack(services: WataService[], ids: string[]): { id: string; name: string; reason: string }[] {
  return services
    .filter((service) => ids.includes(service.id) && service.refusal)
    .map((service) => ({ id: service.id, name: service.name, reason: service.refusal! }))
}

/** ``ids`` in the order the server restarts them, which its list of services follows. */
export function inPlanOrder(services: WataService[], ids: string[]): WataService[] {
  return services.filter((service) => ids.includes(service.id))
}

/** Why the restart of a service cannot be asked from here now; null when it can. */
export function restartUnavailable(state: ServicesState, service?: WataService): string | null {
  if (!state.available) return state.error ?? 'No control service runs beside this admin'
  if (!state.actions_enabled) return 'Operator actions are switched off in the configuration (admin.actions_enabled)'
  if (planRunning(state.plan)) return 'A restart is under way: one at a time'
  if (service?.state === 'absent') return 'Its container is not created: its compose profile is off'
  return null
}

/** What the top bar's "Restart needed" says: the services named when the control service says which. */
export function restartNeededHint(session: Pick<Session, 'restart_services'>): string {
  const services = session.restart_services
  if (!services?.length) {
    return 'The configuration changed since the services started: they apply it at their next start.'
  }
  const names = services.map((service) => service.name)
  return `${listed(names)} ${names.length === 1 ? 'runs' : 'run'} without a change saved since ${names.length === 1 ? 'it' : 'they'} started: restart ${names.length === 1 ? 'it' : 'them'} from the System page.`
}
