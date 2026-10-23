import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { PageHeader, Panel } from '@/components/Panel'
import { QueryState, ServiceStatusBadge } from '@/components/status'
import { useToast } from '@/components/toast-context'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { DependencyTrace } from '@/components/ui/dependency-trace'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDateTime, formatNumber } from '@/lib/format'
import type { Health } from '@/lib/types'

import { ClaudePanel } from './ClaudePanel'
import { LicencesPanel } from './LicencesPanel'
import { SaxoPanel } from './SaxoPanel'
import { MAP_HEIGHT, MAP_LINKS, MAP_WIDTH, serviceMap } from './serviceMap'

// The page that says more of a service, where there is one.
const SERVICE_PAGES: Record<string, string> = { ta_engine: '/ta/monitor' }

function ServicesTable({ health }: { health: Health }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[32rem] text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">Service</th>
            <th className="py-2 pr-4 font-medium">State</th>
            <th className="py-2 font-medium">What was observed</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {health.services.map((service) => (
            <tr key={service.id}>
              <td className="py-2.5 pr-4 font-medium">
                {SERVICE_PAGES[service.id] ? (
                  <Link className="underline-offset-2 hover:underline" to={SERVICE_PAGES[service.id]}>
                    {service.name}
                  </Link>
                ) : (
                  service.name
                )}
              </td>
              <td className="py-2.5 pr-4">
                <ServiceStatusBadge status={service.status} />
              </td>
              <td className="py-2.5 text-muted-foreground">{service.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Queues({ health }: { health: Health }) {
  if (health.queues.length === 0) {
    return <p className="py-4 text-center text-sm text-muted-foreground">RabbitMQ did not answer.</p>
  }
  return (
    <table className="w-full text-left text-xs">
      <thead className="text-muted-foreground">
        <tr>
          <th className="py-2 pr-4 font-medium">Queue</th>
          <th className="py-2 pr-4 text-right font-medium">Waiting messages</th>
          <th className="py-2 text-right font-medium">Consumers</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border/40">
        {health.queues.map((queue) => (
          <tr key={queue.name}>
            <td className="py-2.5 pr-4 font-mono">{queue.name}</td>
            <td className="py-2.5 pr-4 text-right tabular-nums">
              {queue.declared ? formatNumber(queue.messages) : 'not declared'}
            </td>
            <td className="py-2.5 text-right tabular-nums">{formatNumber(queue.consumers)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Watchlist({ health, timeZone }: { health: Health; timeZone: string }) {
  const snapshot = health.watchlist
  if (!snapshot) {
    return <p className="py-4 text-center text-sm text-muted-foreground">No snapshot available.</p>
  }
  const figures: [string, string][] = [
    ['Turbos cached', `${snapshot.warm_entries ?? 0} of ${snapshot.configured_targets ?? 0}`],
    ['Stale entries', formatNumber(snapshot.stale_entries ?? 0)],
    [
      'Price streams',
      `${snapshot.active_stream_subscriptions ?? 0} of ${snapshot.desired_stream_subscriptions ?? 0}`,
    ],
    ['Last refresh', formatDateTime(snapshot.last_refresh_completed_at, timeZone)],
    ['Refresh errors', formatNumber(snapshot.last_refresh_errors?.length ?? 0)],
  ]
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-3">
      {figures.map(([label, value]) => (
        <div key={label}>
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="mt-0.5 font-medium tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function DailyReport() {
  const toast = useToast()
  const [confirming, setConfirming] = useState(false)
  const reportMutation = useMutation({
    mutationFn: api.requestDailyStats,
    onSuccess: () => {
      setConfirming(false)
      toast({ title: 'Daily report requested', description: 'It arrives on Telegram.', variant: 'success' })
    },
  })

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          reportMutation.reset()
          setConfirming(true)
        }}
      >
        Send the daily report now
      </Button>
      <ConfirmLiveAction
        open={confirming}
        title="Send the daily report now"
        confirmLabel="Slide to request"
        doneLabel="Requested"
        pending={reportMutation.isPending}
        error={reportMutation.error?.message}
        onConfirm={() => reportMutation.mutate()}
        onClose={() => setConfirming(false)}
        description={
          <>
            The position monitor sends the daily statistics to Telegram, as the scheduler asks it to at 22:00. It also
            writes today&apos;s row of the <code>trade_performance</code> table, and that row is written only once: asked
            before the last trade of the day, it keeps the figures of this moment.
          </>
        }
      />
    </>
  )
}

export function SystemPage() {
  const { timezone, version, actions_enabled: actionsEnabled } = useSession()
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15_000 })
  const health = healthQuery.data

  return (
    <>
      <PageHeader
        title="System"
        description={
          health
            ? `WATA ${version} · checked ${formatDateTime(health.checked_at, timezone)}`
            : `WATA ${version}`
        }
      />

      <QueryState isPending={healthQuery.isPending} error={healthQuery.error} hasData={!!health}>
        {health && (
          <>
            <Panel
              title="Services"
              description="The admin cannot see the containers themselves. Each state comes from what the service answers, from the consumers on its queue, or from the start and stop lines of its log."
              bodyClassName="pt-2"
            >
              {/* The services as a map, where there is room for it; the table says the same, line by line. */}
              <DependencyTrace
                className="mb-4 hidden lg:flex"
                nodes={serviceMap(health.services)}
                connections={MAP_LINKS}
                width={MAP_WIDTH}
                height={MAP_HEIGHT}
                nodeSize={30}
                label="WATA's services, and what each one calls"
              />
              <ServicesTable health={health} />
            </Panel>

            <div className="grid gap-4 lg:grid-cols-2">
              <Panel title="Queues" bodyClassName="pt-2">
                <Queues health={health} />
              </Panel>
              <Panel title="Watchlist manager">
                <Watchlist health={health} timeZone={timezone} />
              </Panel>
            </div>
          </>
        )}
      </QueryState>

      <SaxoPanel />

      <ClaudePanel />

      <Panel
        title="Daily report"
        description="The statistics the position monitor sends to Telegram at the end of the day."
      >
        {actionsEnabled ? (
          <DailyReport />
        ) : (
          <Alert tone="neutral">
            <AlertDescription>
              Operator actions are switched off in the configuration (<code>admin.actions_enabled</code>).
            </AlertDescription>
          </Alert>
        )}
      </Panel>

      <LicencesPanel />
    </>
  )
}
