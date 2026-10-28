import { Alert, AlertDescription, Badge, formatNumber, Hint, Panel, QueryState } from 'ioiti-ui'
import { useState } from 'react'

import type { ServicesState } from '@/lib/types'

import { RestartButton, RestartDialog, RestartProgress } from './RestartViews'
import { containerSaid, pendingSaid, restartUnavailable, withPendingChanges } from './restarts'
import { useServices } from './useServices'

function ContainersTable({ state, timeZone, onRestart }: { state: ServicesState; timeZone: string; onRestart: (ids: string[]) => void }) {
  return (
    // Positioned: the last column's name, for screen readers alone, stays in the frame that scrolls it.
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[40rem] text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">Service</th>
            <th className="py-2 pr-4 font-medium">Container</th>
            <th className="py-2 pr-4 text-right font-medium">Restarts</th>
            <th className="py-2 pr-4 font-medium">Configuration</th>
            <th className="py-2 font-medium">
              <span className="sr-only">Restart</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {state.services.map((service) => {
            const pending = pendingSaid(service)
            return (
              <tr key={service.id}>
                <td className="py-2.5 pr-4 font-medium">
                  {service.name}
                  {service.trading_path && (
                    <Hint content="Trades, or decides when to: not restarted with a position open or the market open, unless asked anyway">
                      <Badge variant="outline" className="ml-2">
                        Trading
                      </Badge>
                    </Hint>
                  )}
                </td>
                <td className="py-2.5 pr-4 text-muted-foreground">
                  {containerSaid(service, timeZone)}
                  {service.oom_killed && <span className="ml-1 text-negative">· stopped for lack of memory</span>}
                  {service.last_restart && !service.last_restart.ok && (
                    <span className="ml-1 text-negative">· its last restart failed: {service.last_restart.detail}</span>
                  )}
                </td>
                <td className="py-2.5 pr-4 text-right tabular-nums">
                  {service.restart_count === null ? '—' : formatNumber(service.restart_count)}
                </td>
                <td className="py-2.5 pr-4">
                  {pending ? (
                    <Hint content={service.pending.paths.length ? service.pending.paths.join(', ') : undefined}>
                      <span className="text-warning">{pending}</span>
                    </Hint>
                  ) : (
                    <span className="text-muted-foreground">Up to date</span>
                  )}
                </td>
                <td className="py-1.5 text-right">
                  <RestartButton
                    label="Restart"
                    unavailable={restartUnavailable(state, service)}
                    onClick={() => onRestart([service.id])}
                  />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/**
 * WATA's containers as Docker has them, through the control service: since
 * when each runs, the saved changes it does not run with, and its restart.
 */
export function ContainersPanel({ timeZone }: { timeZone: string }) {
  const servicesQuery = useServices()
  const state = servicesQuery.data
  const [asked, setAsked] = useState<string[] | null>(null)
  const pending = state ? withPendingChanges(state) : []

  return (
    <Panel
      id="containers"
      title="Containers"
      description="What Docker says of each of WATA's containers, through the control service, and the saved changes each does not run with yet: a service reads the configuration when it starts."
      action={
        state &&
        pending.length > 0 && (
          <RestartButton
            variant="primary"
            label={`Restart the ${pending.length === 1 ? 'one' : pending.length} with changes`}
            unavailable={restartUnavailable(state)}
            onClick={() => setAsked(pending.map((service) => service.id))}
          />
        )
      }
      bodyClassName="space-y-4 pt-2"
    >
      <QueryState isPending={servicesQuery.isPending} error={servicesQuery.error} hasData={!!state}>
        {state && (
          <>
            {!state.available && (
              <Alert tone="neutral">
                <AlertDescription>
                  {state.error}.{' '}
                  {state.starts_known
                    ? ''
                    : 'The changes not applied are counted from the admin’s own start, as if every service had started with it.'}
                </AlertDescription>
              </Alert>
            )}
            {state.plan && <RestartProgress plan={state.plan} services={state.services} />}
            <ContainersTable state={state} timeZone={timeZone} onRestart={setAsked} />
            <RestartDialog state={state} ids={asked} onClose={() => setAsked(null)} />
          </>
        )}
      </QueryState>
    </Panel>
  )
}
