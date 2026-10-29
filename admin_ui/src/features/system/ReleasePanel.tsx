import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, AlertTitle, Badge, Button, Checkbox, formatDateTime, Hint, Panel, QueryState } from 'ioiti-ui'
import { Rocket } from 'lucide-react'
import { useEffect, useState } from 'react'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { ApiError, api } from '@/lib/api'
import type { ReleaseApply, ReleaseContainer, ReleaseState } from '@/lib/types'

import {
  RECORDING_ROLES,
  RELEASE_STEP_LABEL,
  RELEASE_STEP_TONE,
  applyOutcome,
  applySucceeded,
  applyUnavailable,
  changedFiles,
  heldSaid,
  isCareful,
  reasonsSaid,
  refusingImages,
  releaseSaid,
  requestFollowed,
  waitingContainers,
} from './release'
import { useRelease } from './useRelease'

/** An apply, container by container, as it goes and once it is done. */
export function ApplyProgress({ apply, running, timeZone }: { apply: ReleaseApply; running: boolean; timeZone: string }) {
  const kind = apply.kind === 'rollback' ? 'Rolling back' : apply.kind === 'full' ? 'Restarting everything' : 'Applying'
  const done = apply.kind === 'rollback' ? 'Rollback done' : apply.kind === 'full' ? 'Full restart done' : 'Apply done'
  return (
    <Alert tone={running ? 'info' : applySucceeded(apply) ? 'success' : 'warning'} aria-live="polite">
      <AlertTitle>
        {running ? `${kind} ${apply.version}` : done} · by {apply.by}
        {apply.force ? ', anyway' : ''} · {formatDateTime(apply.finished_at ?? apply.started_at, timeZone)}
      </AlertTitle>
      <AlertDescription>
        <ol className="mt-1 space-y-1.5">
          {apply.steps.map((step) => (
            <li key={step.container} className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium text-foreground">{step.container}</span>
              <Badge tone={RELEASE_STEP_TONE[step.state]} dot={step.state === 'recreating' || step.state === 'starting'}>
                {RELEASE_STEP_LABEL[step.state]}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {step.before ?? '—'} → {step.after}
                {step.detail && step.state !== 'ready' ? ` · ${step.detail}` : ''}
              </span>
            </li>
          ))}
        </ol>
        {!running && <p className="mt-2 text-xs">{applyOutcome(apply)}</p>}
      </AlertDescription>
    </Alert>
  )
}

/**
 * The confirmation of an apply: what holds each container back now, which only "Apply anyway" goes past
 * (recorded in the audit trail), then what changes for each. The server reads the guards again before each
 * container, whatever is said here.
 */
function ApplyDialog({
  state,
  ids,
  onClose,
  onAsked,
}: {
  state: ReleaseState
  ids: string[] | null
  onClose: () => void
  onAsked: (requestId: string) => void
}) {
  const queryClient = useQueryClient()
  const [anyway, setAnyway] = useState(false)
  const entries = waitingContainers(state).filter((entry) => ids?.includes(entry.container))
  const held = entries.filter((entry) => state.held[entry.container])
  const careful = entries.some(isCareful)
  const newest = state.status.newest
  const mutation = useMutation({
    mutationFn: () => api.applyRelease(entries.map((entry) => entry.container), held.length > 0 && anyway),
    onSuccess: async ({ request }) => {
      onAsked(request.id)
      await queryClient.invalidateQueries({ queryKey: ['release'] })
      close()
    },
  })
  const close = () => {
    setAnyway(false)
    mutation.reset()
    onClose()
  }
  return (
    <ConfirmLiveAction
      open={ids !== null && entries.length > 0}
      title={entries.length === 1 ? `Apply ${newest} to ${entries[0].container}` : `Apply ${newest} to ${entries.length} containers`}
      gesture={careful ? 'slide' : 'button'}
      confirmLabel={careful ? 'Slide to apply' : 'Apply'}
      doneLabel="Asked"
      pending={mutation.isPending}
      blocked={held.length > 0 && !anyway}
      error={
        mutation.error instanceof ApiError && mutation.error.status === 403
          ? 'Operator actions are disabled in the configuration (admin.actions_enabled).'
          : (mutation.error?.message ?? null)
      }
      warning={
        careful
          ? 'A container that trades or records stops for seconds to a minute while it is recreated: no signal is taken, no exit decided, no bar recorded by it meanwhile.'
          : 'Each container stops for a few seconds while it is recreated on the new image.'
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
                      <li key={entry.container}>
                        {entry.container}: {state.held[entry.container]}
                      </li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
              <p className="text-xs text-muted-foreground">
                The server checks the same again before each container and refuses unless you apply anyway. The audit
                trail and the deploy&apos;s history then record what held it back.
              </p>
              <Checkbox label="Apply anyway" checked={anyway} onChange={(event) => setAnyway(event.target.checked)} />
            </div>
          )}
          <p>
            The server recreates them one after the other on {newest}&apos;s image, each once the one before is at work
            again; one that does not stay up is put back on its image of before, and the rest waits.
          </p>
          <ul className="space-y-2">
            {entries.map((entry) => (
              <li key={entry.container} className="rounded-lg border border-border/60 px-3 py-2">
                <p className="font-medium text-foreground">
                  {entry.container} <span className="font-normal text-muted-foreground">· {entry.version} → {newest}</span>
                </p>
                <p className="text-xs">Changes {reasonsSaid(entry)}.</p>
              </li>
            ))}
          </ul>
          {entries.some((entry) => entry.container === 'admin1') && (
            <p className="text-xs text-muted-foreground">
              The admin goes last: this page loses its session, sign in again in a few seconds.
            </p>
          )}
        </div>
      }
      onConfirm={() => mutation.mutate()}
      onClose={close}
    />
  )
}

function WaitingTable({
  state,
  onApply,
  unavailable,
}: {
  state: ReleaseState
  onApply: (ids: string[]) => void
  unavailable: string | null
}) {
  const newest = state.status.newest
  return (
    // Positioned: the last column's name, for screen readers alone, stays in the frame that scrolls it.
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[40rem] text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">Container</th>
            <th className="py-2 pr-4 font-medium">Runs</th>
            <th className="py-2 pr-4 font-medium">What {newest} changes for it</th>
            <th className="py-2 pr-4 font-medium">Now</th>
            <th className="py-2 font-medium">
              <span className="sr-only">Apply</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {waitingContainers(state).map((entry: ReleaseContainer) => {
            const held = heldSaid(state, entry)
            const group = entry.group
              ? waitingContainers(state)
                  .filter((other) => other.group === entry.group)
                  .map((other) => other.container)
              : [entry.container]
            return (
              <tr key={entry.container}>
                <td className="py-2.5 pr-4 font-medium">
                  {entry.container}
                  {isCareful(entry) && (
                    <Hint
                      content={
                        RECORDING_ROLES.has(entry.role)
                          ? 'Records what cannot be fetched again: a deploy waits for the market to close, unless asked anyway'
                          : 'Trades, or decides when to: a deploy waits for the market to close and no position open, unless asked anyway'
                      }
                    >
                      <Badge variant="outline" className="ml-2">
                        {RECORDING_ROLES.has(entry.role) ? 'Records' : 'Trading'}
                      </Badge>
                    </Hint>
                  )}
                </td>
                <td className="py-2.5 pr-4 tabular-nums">{entry.version ?? '—'}</td>
                <td className="py-2.5 pr-4 text-muted-foreground">
                  <Hint content={changedFiles(entry) ?? undefined}>
                    <span>{reasonsSaid(entry)}</span>
                  </Hint>
                </td>
                <td className="py-2.5 pr-4">
                  {held ? <span className="text-warning">{held}</span> : <span className="text-positive">May go now</span>}
                </td>
                <td className="py-1.5 text-right">
                  <Hint
                    content={
                      unavailable ??
                      (group.length > 1
                        ? `With ${group.filter((name) => name !== entry.container).join(', ')}: they change because of the same files, and move together`
                        : undefined)
                    }
                    wrap
                  >
                    <Button variant="outline" size="sm" disabled={unavailable !== null} onClick={() => onApply(group)}>
                      Apply
                    </Button>
                  </Hint>
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
 * The newest release installed on the server, what each container runs, and what waits for it: a deploy
 * recreates only the containers it changes, each when nothing holds it back, and the rest waits for an apply
 * from here or from the server (watadeploy apply).
 */
export function ReleasePanel({ timeZone }: { timeZone: string }) {
  const releaseQuery = useRelease()
  const state = releaseQuery.data
  const [asked, setAsked] = useState<string[] | null>(null)
  const [request, setRequest] = useState<{ id: string; at: number } | null>(null)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!request) return
    const timer = window.setInterval(() => setNow(Date.now()), 5_000)
    return () => window.clearInterval(timer)
  }, [request])
  const waiting = state ? waitingContainers(state) : []
  const unavailable = state ? applyUnavailable(state) : null
  const followed = requestFollowed(state, request?.id ?? null, request?.at ?? null, now)
  // The last apply once none runs: what the server did, whoever asked.
  const last = state && !state.applying ? (state.status.last ?? null) : null

  return (
    <Panel
      id="release"
      title="Release"
      description="What each container runs, and what waits for the newest release installed: a deploy recreates only the containers whose code, libraries or compose definition it changes, each once nothing holds it back."
      action={
        state &&
        waiting.length > 0 && (
          <Hint content={unavailable} wrap>
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Rocket className="size-3.5" aria-hidden />}
              disabled={unavailable !== null}
              onClick={() => setAsked(waiting.map((entry) => entry.container))}
            >
              {waiting.length === 1 ? `Apply ${state.status.newest} to the one waiting` : `Apply ${state.status.newest} to the ${waiting.length} waiting`}
            </Button>
          </Hint>
        )
      }
      bodyClassName="space-y-4 pt-2"
    >
      <QueryState isPending={releaseQuery.isPending} error={releaseQuery.error} hasData={!!state}>
        {state && !state.available && (
          <Alert tone="neutral">
            <AlertDescription>{state.error}</AlertDescription>
          </Alert>
        )}
        {state?.available && (
          <>
            <p className="text-sm">
              {releaseSaid(state)}
              {state.status.planned_at && (
                <span className="text-muted-foreground"> Said by the server {formatDateTime(state.status.planned_at, timeZone)}.</span>
              )}
            </p>
            {refusingImages(state).map((image) => (
              <Alert key={image.image} tone="danger">
                <AlertTitle>{image.containers.join(', ') || image.image} would not start on config.json as it is</AlertTitle>
                <AlertDescription>
                  {image.image}: {image.detail}. Apply {state.status.newest} to {image.containers.length === 1 ? 'it' : 'them'}{' '}
                  before {image.containers.length === 1 ? 'it restarts' : 'they restart'}.
                </AlertDescription>
              </Alert>
            ))}
            {(followed?.state === 'refused' || followed?.state === 'lost') && (
              <Alert tone="warning">
                <AlertDescription>Not applied: {followed.detail}</AlertDescription>
              </Alert>
            )}
            {request && !followed && (
              <Alert tone="info">
                <AlertDescription>Asked: the server takes the request within seconds.</AlertDescription>
              </Alert>
            )}
            {state.applying && <ApplyProgress apply={state.applying} running timeZone={timeZone} />}
            {last && <ApplyProgress apply={last} running={false} timeZone={timeZone} />}
            {waiting.length > 0 ? (
              <WaitingTable state={state} onApply={setAsked} unavailable={unavailable} />
            ) : (
              <p className="text-xs text-muted-foreground">Every container runs the code of {state.status.newest}.</p>
            )}
            <ApplyDialog
              state={state}
              ids={asked}
              onClose={() => setAsked(null)}
              onAsked={(id) => setRequest({ id, at: Date.now() })}
            />
          </>
        )}
      </QueryState>
    </Panel>
  )
}
