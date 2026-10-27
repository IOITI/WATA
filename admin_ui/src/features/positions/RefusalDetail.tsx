import { Badge, formatNumber, formatTime } from 'ioiti-ui'
import { Link } from 'react-router'

import type { Refusal } from '@/lib/types'

import { orderLink } from './orderLife'
import { REQUEST_RESULTS, answerText, refusalSummary, runLabel } from './orderRequests'
import { PositionPeekLink } from './PositionPeek'
import { RequestList } from './RequestList'

const LINK = 'font-mono text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground'

/** An opened run of attempts Saxo did not take: what happened, Saxo's answers counted, every attempt. */
export function RefusalDetail({ run, timeZone }: { run: Refusal; timeZone: string }) {
  const attemptsShown = run.requests.filter((request) => request.operation === 'place').length
  return (
    <div className="space-y-3 text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge tone={REQUEST_RESULTS[run.outcome].tone}>{runLabel(run)}</Badge>
        {run.position_id && (
          <span className="text-muted-foreground">
            Position{' '}
            <PositionPeekLink id={run.position_id} className={LINK}>
              {run.position_id}
            </PositionPeekLink>
          </span>
        )}
        {run.followed_by?.order_id && (
          <span className="text-muted-foreground">
            then order{' '}
            <Link to={orderLink(run.followed_by.order_id)} className={LINK}>
              {run.followed_by.order_id}
            </Link>
          </span>
        )}
        {run.signal_uuid && (
          <span className="text-muted-foreground">
            Signal <span className="font-mono text-foreground/80">{run.signal_uuid}</span>
          </span>
        )}
      </div>
      <p className="max-w-[60rem] text-foreground/80">{refusalSummary(run, timeZone)}</p>
      {run.reason && <p className="max-w-[60rem] text-muted-foreground">Why WATA sent it: {run.reason}</p>}
      {run.signal?.reason && (
        <p className="max-w-[60rem] text-muted-foreground">What the trader journaled for the signal: {run.signal.reason}</p>
      )}

      <div className="max-w-[60rem] space-y-1">
        <p className="font-medium text-foreground">Saxo’s answers</p>
        <ul aria-label="Saxo’s answers" className="divide-y divide-border/40">
          {run.answers.map((answer) => (
            <li
              key={[answer.outcome, answer.http_status, answer.error_code, answer.error_message].join('|')}
              className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-1"
            >
              <span className="min-w-0 [overflow-wrap:anywhere]">{answerText(answer)}</span>
              <span className="whitespace-nowrap text-muted-foreground tabular-nums">
                {answer.count === 1
                  ? `once, at ${formatTime(answer.first_at, timeZone)}`
                  : `${formatNumber(answer.count)} times, ${formatTime(answer.first_at, timeZone)} to ${formatTime(answer.last_at, timeZone)}`}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="space-y-1">
        <p className="font-medium text-foreground">Every attempt</p>
        <RequestList
          requests={run.requests}
          timeZone={timeZone}
          label={`The attempts of ${runLabel(run).toLowerCase()}`}
          hidden={run.requests_total - attemptsShown}
        />
      </div>
    </div>
  )
}
