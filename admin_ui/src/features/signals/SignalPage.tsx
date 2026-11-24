// One signal's own page (/signals/<uuid> or /signals/engine-<id>): every moment recorded of it, from its bar's close to
// its position seen at Saxo, the orders it gave and the sales sent while it was handled, with a link to each page that
// says more of one of them. GET /api/signals/{ref} (src/admin/signal_trace.py).

import { useQuery } from '@tanstack/react-query'
import { Badge, ButtonLink, formatDateTime, formatWait, KpiStrip, PageHeader, Panel, QueryState } from 'ioiti-ui'
import { Link, useParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { ActionBadge } from '@/components/status'
import { AgentSheet } from '@/features/agent/AgentSheet'
import { AskAgentLink } from '@/features/agent/AskAgentLink'
import { useSession } from '@/features/auth/auth-context'
import { localDay } from '@/features/backtests/model'
import { LinkedPosition, MomentsTimeline, OrderHeading } from '@/features/positions/OrderTimeline'
import { orderLife, orderLink } from '@/features/positions/orderLife'
import { PositionPeekLink } from '@/features/positions/PositionPeek'
import { RequestList } from '@/features/positions/RequestList'
import { monitorHref } from '@/features/ta/monitor/monitorModel'
import { liveCheckDayHref } from '@/features/ta/signals/liveCheckModel'
import { rulePath } from '@/features/ta/signals/ruleModel'
import { RulePeekName } from '@/features/ta/signals/RulePeek'
import { api, isNotFound } from '@/lib/api'
import type { Order, SignalTrace } from '@/lib/types'

import { fateBadge, sourceSaid, stillUnderWay, traceFigures, traceName, traceTimeline } from './signalTrace'

const LINK = 'font-mono text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground'

const SIGNAL_NOT_FOUND = {
  title: 'No such signal',
  description: 'Neither the TA engine nor the trader recorded a signal at this address: it may be mistyped.',
  suggestions: [
    { label: 'Signals', href: '/signals', description: 'The signals the trader received, and what it did with each' },
    { label: 'Signal rules', href: '/ta/signals', description: 'The rules the TA engine runs, each with its journal' },
  ],
}

/** An order the signal gave, said in a line with what became of it, its position and its requests to Saxo. */
function OrderSaid({ order, heading, timeZone }: { order: Order; heading: string; timeZone: string }) {
  return (
    <div className="space-y-2 text-xs">
      <OrderHeading order={order} life={orderLife(order)} timeZone={timeZone} heading={heading} />
      {order.position ? (
        <LinkedPosition position={order.position} currency={order.instrument_currency} timeZone={timeZone} />
      ) : (
        order.position_id && (
          <p className="text-muted-foreground">
            Position{' '}
            <PositionPeekLink id={order.position_id} className={LINK}>
              {order.position_id}
            </PositionPeekLink>
          </p>
        )
      )}
    </div>
  )
}

/** What it is, where it came from and what became of it, in a line under the page's title. */
function Identity({ trace, timeZone }: { trace: SignalTrace; timeZone: string }) {
  const fate = fateBadge(trace.fate.key)
  const engine = trace.engine
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <ActionBadge action={trace.action} />
        {trace.indice && <span className="text-muted-foreground">{trace.indice}</span>}
        <Badge tone={fate.tone}>{fate.label}</Badge>
        {trace.fate.reason && <span className="text-foreground/80">{trace.fate.reason}</span>}
      </div>
      <dl className="flex flex-wrap gap-x-8 gap-y-2 text-xs text-muted-foreground">
        <div>
          <dt>From</dt>
          <dd className="text-foreground/80">
            {trace.source.kind === 'engine' && trace.source.rule_id ? (
              <RulePeekName id={trace.source.rule_id} name={trace.source.rule_name ?? trace.source.rule_id} />
            ) : (
              sourceSaid(trace)
            )}
          </dd>
        </div>
        {engine?.alert_title && (
          <div>
            <dt>Its alert</dt>
            <dd className="text-foreground/80">{engine.alert_title}</dd>
          </div>
        )}
        {engine && (
          <div>
            <dt>Its script, as it ran</dt>
            <dd>
              <Link to={`/ta/indicators/${encodeURIComponent(engine.script_id)}`} className={LINK}>
                v{engine.script_version}
              </Link>
              <span className="text-foreground/80">
                {' '}· {engine.asset} · {engine.side} · {engine.bars}
              </span>
            </dd>
          </div>
        )}
        {engine?.bar_closed_at && (
          <div>
            <dt>Its bar closed</dt>
            <dd className="tabular-nums text-foreground/80">{formatDateTime(engine.bar_closed_at, timeZone)}</dd>
          </div>
        )}
        {trace.signal_uuid && (
          <div>
            <dt>Stored by the web server as</dt>
            <dd className="font-mono text-foreground/80">{trace.signal_uuid}</dd>
          </div>
        )}
      </dl>
    </div>
  )
}

/** The pages that say more of it: its rule, its live day, the Engine monitor, every signal, the AI agent. */
function WhereNext({ trace, timeZone }: { trace: SignalTrace; timeZone: string }) {
  const rule = trace.source.kind === 'engine' ? trace.source.rule_id : null
  const bar = trace.engine?.bar_closed_at ?? trace.journal?.received_timestamp ?? null
  const day = bar ? localDay(bar, timeZone) : null
  return (
    <>
      {rule && (
        <ButtonLink to={rulePath(rule)} variant="outline" size="sm">
          Its rule
        </ButtonLink>
      )}
      {rule && day && trace.source.mode === 'live' && (
        <ButtonLink to={liveCheckDayHref(rule, day)} variant="outline" size="sm">
          Its day: live, paper, backtest
        </ButtonLink>
      )}
      {rule && (
        <ButtonLink to={monitorHref({ rule })} variant="outline" size="sm">
          Engine monitor
        </ButtonLink>
      )}
      <ButtonLink to="/signals" variant="outline" size="sm">
        All signals
      </ButtonLink>
      {trace.signal_uuid && (
        <AskAgentLink subject={{ kind: 'signal', id: trace.signal_uuid, ...(day ? { day } : {}) }} label="Ask the AI agent" />
      )}
    </>
  )
}

export function SignalPage() {
  const { ref = '' } = useParams()
  const { timezone } = useSession()
  const query = useQuery({
    queryKey: ['signal-trace', ref],
    queryFn: () => api.signalTrace(ref),
    retry: false,
    // Read again while it is still on its way: the trader has not journaled it yet, or its order is not settled.
    refetchInterval: (current) => (current.state.data && stillUnderWay(current.state.data, Date.now()) ? 5_000 : false),
  })
  const trace = query.data

  if (isNotFound(query.error)) return <NotFoundState {...SIGNAL_NOT_FOUND} />

  const name = trace ? traceName(trace, (value) => formatDateTime(value, timezone)) : 'Signal'
  const sales = trace?.sold_for_it ?? []
  // Only the stretches it went through: a signal that never reached the trader has none of its own.
  const figures = trace ? traceFigures(trace).filter((figure) => figure.ms != null) : []
  return (
    <>
      <div className="flex flex-col gap-6">
        <PageHeader
          title={name}
          description={trace ? `${sourceSaid(trace)}: every moment recorded of it, from its bar to its position.` : undefined}
          actions={trace ? <WhereNext trace={trace} timeZone={timezone} /> : undefined}
        />
        <QueryState isPending={query.isPending} error={query.error} hasData={Boolean(trace)}>
          {trace && (
            <div className="flex flex-col gap-6">
              <Identity trace={trace} timeZone={timezone} />
              {figures.length > 0 && (
                <KpiStrip
                  items={figures.map((figure) => ({
                    label: figure.label,
                    value: formatWait(Math.max(0, figure.ms ?? 0)),
                    hint: figure.hint,
                  }))}
                />
              )}
              <Panel
                title="Its way, moment by moment"
                description="Each moment on the server's clock, which the engine and the trader share, unless it says Saxo's or TradingView's. Between the web server receiving it and the order leaving, the trader's own steps are not recorded apart."
              >
                <MomentsTimeline items={traceTimeline(trace, timezone)} label="The moments of this signal" />
                {trace.run_missing && <p className="mt-3 text-xs text-muted-foreground">{trace.run_missing}.</p>}
              </Panel>
              {trace.orders.length > 0 && (
                <Panel title="What it bought" description="The order it gave, with what became of it, and its requests to Saxo.">
                  <div className="flex flex-col gap-4">
                    {trace.orders.map((order) => (
                      <OrderSaid key={order.order_id} order={order} heading="Bought by order" timeZone={timezone} />
                    ))}
                    {trace.requests.length > 0 && (
                      <div className="space-y-1 text-xs">
                        <p className="font-medium text-foreground">Requests to Saxo for it</p>
                        <RequestList requests={trace.requests} timeZone={timezone} label="The requests sent to Saxo for this signal" />
                      </div>
                    )}
                  </div>
                </Panel>
              )}
              {trace.orders.length === 0 && trace.requests.length > 0 && (
                <Panel title="Its requests to Saxo" description="Saxo took none of them: no order, no position.">
                  <RequestList requests={trace.requests} timeZone={timezone} label="The requests sent to Saxo for this signal" />
                </Panel>
              )}
              {sales.length > 0 && (
                <Panel
                  title="Sold while it was handled"
                  description="The sales the trader sent between receiving this signal and journaling it: the other side before an entry, or what a close asked for."
                >
                  <div className="flex flex-col gap-4">
                    {sales.map((sale) =>
                      sale.order ? (
                        <OrderSaid key={sale.order.order_id} order={sale.order} heading="Sold by order" timeZone={timezone} />
                      ) : (
                        <div key={sale.position_id ?? 'none'} className="space-y-1 text-xs">
                          <p className="text-muted-foreground">
                            Position{' '}
                            {sale.position_id ? (
                              <PositionPeekLink id={sale.position_id} className={LINK}>
                                {sale.position_id}
                              </PositionPeekLink>
                            ) : (
                              'unknown'
                            )}
                            : no sale recorded as taken.{' '}
                            {sale.position_id && (
                              <Link to={orderLink(sale.position_id)} className="underline decoration-current/40 underline-offset-2">
                                Its orders
                              </Link>
                            )}
                          </p>
                          <RequestList requests={sale.requests} timeZone={timezone} label={`The sales of position ${sale.position_id ?? ''}`} />
                        </div>
                      ),
                    )}
                  </div>
                </Panel>
              )}
            </div>
          )}
        </QueryState>
      </div>
      <AgentSheet />
    </>
  )
}
