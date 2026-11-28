import { Alert, AlertDescription, Badge, formatNumber, Hint, Panel, QueryState, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from 'ioiti-ui'
import { useState } from 'react'

import type { ReleaseContainer, ServicesState } from '@/lib/types'

import { RestartButton, RestartDialog, RestartProgress } from './RestartViews'
import { reasonsSaid, versionSaid } from './release'
import { containerSaid, pendingSaid, restartUnavailable, withPendingChanges } from './restarts'
import { useRelease } from './useRelease'
import { useServices } from './useServices'

function ContainersTable({
  state,
  timeZone,
  onRestart,
  releases,
  newest,
}: {
  state: ServicesState
  timeZone: string
  onRestart: (ids: string[]) => void
  /** What each container runs, by its name, as watadeploy says it; empty before it said anything. */
  releases: Record<string, ReleaseContainer>
  newest: string | undefined
}) {
  return (
    // Positioned: the last column's name, for screen readers alone, stays in the frame that scrolls it.
    <Table density="compact" className="min-w-[40rem]">
      <TableHead>
        <TableRow>
          <TableHeaderCell>Service</TableHeaderCell>
          <TableHeaderCell>Container</TableHeaderCell>
          <TableHeaderCell>Release</TableHeaderCell>
          <TableHeaderCell numeric>Restarts</TableHeaderCell>
          <TableHeaderCell>Configuration</TableHeaderCell>
          <TableHeaderCell>
            <span className="sr-only">Restart</span>
          </TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {state.services.map((service) => {
          const pending = pendingSaid(service)
          const release = service.container ? releases[service.container] : undefined
          const waits = release?.kind === 'change' || release?.kind === 'optional'
          return (
            <TableRow key={service.id}>
              <TableCell className="font-medium">
                {service.name}
                {service.trading_path && (
                  <Hint content="Trades, or decides when to: not restarted with a position open or the market open, unless asked anyway">
                    <Badge variant="outline" className="ml-2">
                      Trading
                    </Badge>
                  </Hint>
                )}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {containerSaid(service, timeZone)}
                {service.oom_killed && <span className="ml-1 text-negative">· stopped for lack of memory</span>}
                {service.last_restart && !service.last_restart.ok && (
                  <span className="ml-1 text-negative">· its last restart failed: {service.last_restart.detail}</span>
                )}
              </TableCell>
              <TableCell className="tabular-nums">
                {waits && release ? (
                  <Hint content={`What ${newest} changes for it: ${reasonsSaid(release)}. The Release panel above applies it.`}>
                    <span className="text-warning">{versionSaid(release, newest)}</span>
                  </Hint>
                ) : (
                  <span className="text-muted-foreground">{versionSaid(release, newest)}</span>
                )}
              </TableCell>
              <TableCell numeric>
                {service.restart_count === null ? '—' : formatNumber(service.restart_count)}
              </TableCell>
              <TableCell>
                {pending ? (
                  <Hint content={service.pending.paths.length ? service.pending.paths.join(', ') : undefined}>
                    <span className="text-warning">{pending}</span>
                  </Hint>
                ) : (
                  <span className="text-muted-foreground">Up to date</span>
                )}
              </TableCell>
              <TableCell className="py-1.5 text-right">
                <RestartButton
                  label="Restart"
                  unavailable={restartUnavailable(state, service)}
                  onClick={() => onRestart([service.id])}
                />
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

/**
 * WATA's containers as Docker has them, through the control service: since
 * when each runs, the saved changes it does not run with, and its restart.
 */
export function ContainersPanel({ timeZone }: { timeZone: string }) {
  const servicesQuery = useServices()
  const state = servicesQuery.data
  const release = useRelease().data
  const releases = Object.fromEntries((release?.status.containers ?? []).map((entry) => [entry.container, entry]))
  const [asked, setAsked] = useState<string[] | null>(null)
  const pending = state ? withPendingChanges(state) : []

  return (
    <Panel
      id="containers"
      title="Containers"
      description="What Docker says of each of WATA's containers, through the control service, the release each runs, and the saved changes each does not run with yet: a service reads the configuration when it starts, and a restart keeps the release it runs."
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
            <ContainersTable
              state={state}
              timeZone={timeZone}
              onRestart={setAsked}
              releases={releases}
              newest={release?.status.newest}
            />
            <RestartDialog state={state} ids={asked} onClose={() => setAsked(null)} />
          </>
        )}
      </QueryState>
    </Panel>
  )
}
