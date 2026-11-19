// The Disk page's cleanups (src/admin/disk_cleanups.py): what each would give back, and asking it. The server's
// is watadeploy's own cleanup on the host, asked by a request that names nothing but itself; the page follows it
// through the release's status (the `release` live event), and Telegram says what it gave back.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Button, formatBytes, formatDateTime, Hint, Panel, QueryState } from 'ioiti-ui'
import { useState } from 'react'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { useSession } from '@/features/auth/auth-context'
import { api, ApiError } from '@/lib/api'
import type { DiskCleanupId, DiskCleanupOffer } from '@/lib/types'

import { CLEANUP_BUTTON, CLEANUP_TITLE, cleanupGain, cleanupSentence, keptSentence, lastCleanupSentence } from './diskModel'

const WARNING: Record<DiskCleanupId, string> = {
  server: 'This acts on the server: Docker and the release folders. Running containers are never touched.',
  signal_cache: 'Backtests asked again on the same days will run their scripts anew.',
  former: 'What the former backtester left cannot be brought back.',
}

function errorOf(error: unknown): string | null {
  if (!error) return null
  if (error instanceof ApiError && error.status === 403) return 'Operator actions are disabled in the configuration (admin.actions_enabled).'
  return error instanceof Error ? error.message : String(error)
}

function Offer({ offer, onAsk, actionsEnabled }: { offer: DiskCleanupOffer; onAsk: () => void; actionsEnabled: boolean }) {
  const { timezone } = useSession()
  const moment = (iso: string) => formatDateTime(iso, timezone, false)
  const kept = keptSentence(offer)
  const why = !actionsEnabled ? 'Operator actions are disabled in the configuration (admin.actions_enabled)' : offer.waiting
    ? 'Asked: watadeploy takes it within a minute'
    : offer.why_not
  const button = (
    <Button variant="secondary" size="sm" disabled={!offer.available || !actionsEnabled} onClick={onAsk}>
      {CLEANUP_BUTTON[offer.id]}
    </Button>
  )
  return (
    <li className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1">
        <p className="font-medium">
          {CLEANUP_TITLE[offer.id]} <span className="font-normal text-muted-foreground">· {cleanupGain(offer)}</span>
        </p>
        <p className="text-sm text-muted-foreground">{cleanupSentence(offer)}</p>
        {kept && <p className="text-xs text-muted-foreground">{kept}</p>}
        {offer.waiting && <p className="text-xs">Asked {moment(offer.waiting.at)}: waiting for watadeploy.</p>}
        {offer.last && <p className="text-xs text-muted-foreground">{lastCleanupSentence(offer.last, moment)}</p>}
      </div>
      <div className="shrink-0">{why ? <Hint content={why} wrap>{button}</Hint> : button}</div>
    </li>
  )
}

export function DiskCleanupsPanel() {
  const queryClient = useQueryClient()
  const [asking, setAsking] = useState<DiskCleanupOffer | null>(null)
  const query = useQuery({
    queryKey: ['disk-cleanups'],
    queryFn: api.diskCleanups,
    // A cleanup asked of the server is followed until watadeploy has done it.
    refetchInterval: (state) => (state.state.data?.items.some((offer) => offer.waiting) ? 5_000 : 60_000),
  })
  const mutation = useMutation({
    mutationFn: (action: DiskCleanupId) => api.diskCleanup(action),
    onSuccess: async () => {
      await Promise.all(['disk-cleanups', 'disk', 'backtests'].map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
    },
  })
  const offers = query.data?.items ?? []
  return (
    <Panel
      id="cleanups"
      title="Give space back"
      description="What can go without changing a retention. The estimates come from the newest sample and the services' own counts."
    >
      <QueryState isPending={query.isPending} error={query.error} hasData={Boolean(query.data)}>
        {offers.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is offered by this admin.</p>
        ) : (
          <ul className="divide-y divide-border/60">
            {offers.map((offer) => (
              <Offer
                key={offer.id}
                offer={offer}
                actionsEnabled={query.data?.actions_enabled ?? false}
                onAsk={() => {
                  mutation.reset()
                  setAsking(offer)
                }}
              />
            ))}
          </ul>
        )}
        {mutation.isSuccess && asking && (
          <Alert tone="neutral" className="mt-4">
            <AlertDescription>
              {asking.id === 'server'
                ? 'Asked: watadeploy cleans up within a minute and says on Telegram what it gave back.'
                : `Done: ${formatBytes(typeof mutation.data?.bytes === 'number' ? mutation.data.bytes : null)} given back.`}
            </AlertDescription>
          </Alert>
        )}
      </QueryState>
      <ConfirmLiveAction
        open={asking !== null && !mutation.isSuccess}
        title={asking ? CLEANUP_TITLE[asking.id] : ''}
        description={asking ? `${cleanupSentence(asking)} ${cleanupGain(asking)}.` : ''}
        warning={asking ? WARNING[asking.id] : undefined}
        gesture="slide"
        confirmLabel="Slide to clean up"
        doneLabel={asking?.id === 'server' ? 'Asked' : 'Done'}
        pending={mutation.isPending}
        error={errorOf(mutation.error)}
        onConfirm={() => asking && mutation.mutate(asking.id)}
        onClose={() => setAsking(null)}
      />
    </Panel>
  )
}
