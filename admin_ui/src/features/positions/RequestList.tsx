import { Badge } from '@/components/ui/badge'
import { formatClock, formatNumber, formatWait } from '@/lib/format'
import type { OrderRequest } from '@/lib/types'
import { cn } from '@/lib/utils'

import { REQUEST_RESULTS, requestLabel, requestTookMs, saxoAnswer } from './orderRequests'

// Time and request side by side, the result and Saxo's answer under them on a phone; one line from a tablet up.
const REQUEST_GRID =
  'grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-0.5 sm:grid-cols-[7.5rem_minmax(0,1fr)_10rem_minmax(0,1.6fr)]'

const SERVICES: Record<string, string> = { trader: 'trader', position_monitor: 'position monitor' }

/** A body as it was kept, laid out when it is JSON. */
function body(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

function Sent({ request }: { request: OrderRequest }) {
  return (
    <details className="col-span-full mt-1 text-muted-foreground">
      <summary className="w-fit cursor-pointer select-none hover:text-foreground">What was sent and received</summary>
      <dl className="mt-1 grid gap-x-4 gap-y-1 sm:grid-cols-[9rem_minmax(0,1fr)]">
        {request.reason && (
          <>
            <dt>Why WATA sent it</dt>
            <dd className="text-foreground/80">{request.reason}</dd>
          </>
        )}
        {request.signal_uuid && (
          <>
            <dt>Signal</dt>
            <dd className="font-mono text-foreground/80">{request.signal_uuid}</dd>
          </>
        )}
        <dt>Sent to Saxo</dt>
        <dd>
          {request.request_body ? (
            <pre className="max-h-48 overflow-auto rounded-md bg-muted/40 p-2 font-mono text-[0.7rem] text-foreground/80">
              {body(request.request_body)}
            </pre>
          ) : (
            '—'
          )}
        </dd>
        <dt>Saxo’s answer</dt>
        <dd>
          {request.response_body ? (
            <pre className="max-h-48 overflow-auto rounded-md bg-muted/40 p-2 font-mono text-[0.7rem] text-foreground/80 [overflow-wrap:anywhere] whitespace-pre-wrap">
              {body(request.response_body)}
            </pre>
          ) : (
            'Nothing came back'
          )}
        </dd>
      </dl>
    </details>
  )
}

interface RequestListProps {
  requests: OrderRequest[]
  timeZone: string
  /** For screen readers: whose requests these are. */
  label: string
  /** How many earlier ones are not given: only the latest are, when there are many. */
  hidden?: number
}

/** Requests sent to Saxo's order endpoint, a line each: when, what, by which service, Saxo's answer. */
export function RequestList({ requests, timeZone, label, hidden = 0 }: RequestListProps) {
  if (requests.length === 0) return null
  return (
    <div className="max-w-[60rem]">
      <div
        aria-hidden="true"
        className={cn(REQUEST_GRID, 'hidden border-b border-border/40 py-1 font-medium text-muted-foreground sm:grid')}
      >
        <span>Time</span>
        <span>Request</span>
        <span>Result</span>
        <span>What Saxo said</span>
      </div>
      <ol aria-label={label} className="divide-y divide-border/40">
        {requests.map((request) => {
          const result = REQUEST_RESULTS[request.outcome ?? 'error']
          const took = requestTookMs(request)
          return (
            <li key={request.request_id} className={cn(REQUEST_GRID, 'grid py-1.5')}>
              <span className="whitespace-nowrap tabular-nums">{formatClock(request.requested_at, timeZone)}</span>
              <span className="min-w-0">
                {requestLabel(request)}
                {request.recorded_by && (
                  <span className="text-muted-foreground"> · {SERVICES[request.recorded_by] ?? request.recorded_by}</span>
                )}
              </span>
              <span className="flex flex-wrap items-center gap-x-2">
                <Badge tone={result.tone}>{result.label}</Badge>
                {took != null && <span className="text-muted-foreground tabular-nums">{formatWait(took)}</span>}
              </span>
              <span
                className={cn(
                  'text-muted-foreground [overflow-wrap:anywhere]',
                  request.outcome !== 'accepted' && 'text-foreground/90',
                )}
              >
                {saxoAnswer(request)}
              </span>
              <Sent request={request} />
            </li>
          )
        })}
      </ol>
      {hidden > 0 && (
        <p className="mt-1 text-muted-foreground">
          {hidden === 1 ? 'One earlier request is' : `${formatNumber(hidden)} earlier requests are`} not shown.
        </p>
      )}
    </div>
  )
}
