import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, AlertTitle, Badge, Button, Checkbox, Hint } from 'ioiti-ui'
import { RotateCw } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { ApiError, api } from '@/lib/api'
import type { RestartPlan, ServicesState, WataService } from '@/lib/types'

import {
  STEP_LABEL,
  STEP_TONE,
  heldBack,
  inPlanOrder,
  pendingSaid,
  planOutcome,
  planRunning,
  planSucceeded,
  restartUnavailable,
} from './restarts'
import { useServices } from './useServices'

/**
 * The confirmation of a restart of one service or several: what holds it
 * back now, which only "Restart anyway" goes past (recorded in the audit
 * trail) unless those held back are left out, then what each costs and what
 * it does not run with yet.
 */
export function RestartDialog({
  state,
  ids,
  onClose,
  onStarted,
}: {
  state: ServicesState
  /** The services to restart; none: the dialog is closed. */
  ids: string[] | null
  onClose: () => void
  /** The plan the server started. */
  onStarted?: (plan: RestartPlan) => void
}) {
  const queryClient = useQueryClient()
  const [anyway, setAnyway] = useState(false)
  // Those held back left out: the others restarted now, they once nothing holds them back.
  const [leftOut, setLeftOut] = useState(false)
  const asked = inPlanOrder(state.services, ids ?? [])
  const heldNow = heldBack(state.services, ids ?? [])
  const services = leftOut ? asked.filter((service) => !heldNow.some((entry) => entry.id === service.id)) : asked
  const held = leftOut ? [] : heldNow
  const others = asked.length - heldNow.length
  const tradingPath = services.some((service) => service.trading_path)
  const mutation = useMutation({
    mutationFn: () => api.restartServices(services.map((service) => service.id), held.length > 0 && anyway),
    onSuccess: async ({ plan }) => {
      onStarted?.(plan)
      await Promise.all([['services'], ['session']].map((queryKey) => queryClient.invalidateQueries({ queryKey })))
      close()
    },
  })
  const close = () => {
    setAnyway(false)
    setLeftOut(false)
    mutation.reset()
    onClose()
  }
  const title =
    services.length === 1 ? `Restart the ${services[0].name.toLowerCase()}` : `Restart ${services.length} services`

  return (
    <ConfirmLiveAction
      open={ids !== null && services.length > 0}
      title={title}
      gesture={tradingPath ? 'slide' : 'button'}
      confirmLabel={tradingPath ? 'Slide to restart' : 'Restart'}
      doneLabel="Asked"
      pending={mutation.isPending}
      blocked={held.length > 0 && !anyway}
      error={
        mutation.error instanceof ApiError && mutation.error.status === 403
          ? 'Operator actions are disabled in the configuration (admin.actions_enabled).'
          : (mutation.error?.message ?? null)
      }
      warning={
        tradingPath
          ? 'A service of the trading path stops for seconds to a minute: no signal is taken and no exit decided by it meanwhile.'
          : 'Each service stops for a few seconds, then reads the configuration again.'
      }
      description={
        <div className="space-y-3">
          {held.length > 0 && (
            <div className="space-y-2">
              <Alert tone="danger">
                <AlertTitle>Held back now</AlertTitle>
                <AlertDescription>
                  <ul className="list-disc pl-5">
                    {held.map((entry) => (
                      <li key={entry.id}>
                        {entry.name}: {entry.reason}
                      </li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
              <p className="text-xs text-muted-foreground">
                The server checks the same, again before each service of the plan, and refuses unless you restart anyway.
                The audit trail then records what held it back.
              </p>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <Checkbox label="Restart anyway" checked={anyway} onChange={(event) => setAnyway(event.target.checked)} />
                {others > 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setAnyway(false)
                      setLeftOut(true)
                    }}
                  >
                    Leave {held.length === 1 ? 'it' : 'them'} out: restart the {others === 1 ? 'other' : `${others} others`}
                  </Button>
                )}
              </div>
            </div>
          )}
          {leftOut && (
            <p className="text-xs text-muted-foreground">
              Left out, held back now: {heldNow.map((entry) => entry.name).join(', ')}. Restart{' '}
              {heldNow.length === 1 ? 'it' : 'them'} once nothing holds {heldNow.length === 1 ? 'it' : 'them'} back.{' '}
              <button type="button" className="underline underline-offset-2" onClick={() => setLeftOut(false)}>
                Put {heldNow.length === 1 ? 'it' : 'them'} back
              </button>
            </p>
          )}
          <p>
            {services.length > 1
              ? 'One after the other, each once the one before is at work again; the plan stops at the first that is not.'
              : 'Docker stops it, as a deploy does, and starts it again: it reads the configuration as it is now.'}
          </p>
          <ul className="space-y-2">
            {services.map((service) => (
              <li key={service.id} className="rounded-lg border border-border/60 px-3 py-2">
                <p className="font-medium text-foreground">{service.name}</p>
                <p className="text-xs">{service.cost}</p>
                {pendingSaid(service) && <p className="mt-1 text-xs text-muted-foreground">Now: {pendingSaid(service)}.</p>}
              </li>
            ))}
          </ul>
          {services.some((service) => service.id === 'admin') && (
            <p className="text-xs text-muted-foreground">
              The admin restarts last: this page loses its session, sign in again in a few seconds.
            </p>
          )}
        </div>
      }
      onConfirm={() => mutation.mutate()}
      onClose={close}
    />
  )
}

/** A plan of restarts, service by service, as it goes and once it is done. */
export function RestartProgress({ plan, services }: { plan: RestartPlan; services: WataService[] }) {
  const names = Object.fromEntries(services.map((service) => [service.id, service.name]))
  const running = planRunning(plan)
  return (
    <Alert tone={running ? 'info' : planSucceeded(plan) ? 'success' : 'warning'} aria-live="polite">
      <AlertTitle>{running ? 'Restarting' : 'Restart done'}</AlertTitle>
      <AlertDescription>
        <ol className="mt-1 space-y-1.5">
          {plan.steps.map((step) => (
            <li key={step.service} className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium text-foreground">{names[step.service] ?? step.service}</span>
              <Badge tone={STEP_TONE[step.state]} dot={step.state === 'restarting' || step.state === 'starting'}>
                {STEP_LABEL[step.state]}
              </Badge>
              {step.detail && <span className="text-xs text-muted-foreground">{step.detail}</span>}
            </li>
          ))}
        </ol>
        {!running && <p className="mt-2 text-xs">{planOutcome(plan, names)}</p>}
      </AlertDescription>
    </Alert>
  )
}

/** A Restart button, disabled with why when the restart cannot be asked from here. */
export function RestartButton({
  label,
  unavailable,
  onClick,
  variant = 'outline',
}: {
  label: string
  unavailable: string | null
  onClick: () => void
  variant?: 'outline' | 'primary'
}) {
  return (
    <Hint content={unavailable} wrap>
      <Button
        variant={variant}
        size="sm"
        leftIcon={<RotateCw className="size-3.5" aria-hidden />}
        disabled={unavailable !== null}
        onClick={onClick}
      >
        {label}
      </Button>
    </Hint>
  )
}

/**
 * Where a change is made: a button that restarts the services that apply it,
 * its confirmation, and the progress of the plan it asked for. Without the
 * control service, the System page says why.
 */
export function RestartAction({ ids, label }: { ids: string[]; label?: string }) {
  const servicesQuery = useServices(ids.length > 0)
  const [asked, setAsked] = useState<string[] | null>(null)
  // The plan asked from here, by its start as the server gave it: a plan of before is not this one's.
  const [started, setStarted] = useState<string | null>(null)
  const state = servicesQuery.data
  if (!state) return null
  const created = inPlanOrder(state.services, ids).filter((service) => service.state !== 'absent')
  const plan =
    state.plan &&
    (state.plan.started_at === started || (planRunning(state.plan) && state.plan.services.some((id) => ids.includes(id))))
      ? state.plan
      : null
  if (!state.available) {
    return (
      <p className="text-xs text-muted-foreground">
        {state.error}. <Link className="underline underline-offset-2" to="/system">System</Link> lists the containers.
      </p>
    )
  }
  return (
    <div className="space-y-2">
      {created.length > 0 && !plan && (
        <RestartButton
          variant="primary"
          label={label ?? (created.length === 1 ? `Restart the ${created[0].name.toLowerCase()}` : `Restart these ${created.length} services`)}
          unavailable={restartUnavailable(state)}
          onClick={() => setAsked(created.map((service) => service.id))}
        />
      )}
      {plan && <RestartProgress plan={plan} services={state.services} />}
      <RestartDialog state={state} ids={asked} onClose={() => setAsked(null)} onStarted={(found) => setStarted(found.started_at)} />
    </div>
  )
}
