import type { Order, OrderRequest, SignalTrace } from '@/lib/types'

/** One signal of the engine as GET /api/signals/{ref} answers it: it sold position 4001, then bought 5001. */
export const UUID = '0b8f6a52-6c1e-4d55-9a8e-3f1c2d4e5a6b'

/** The buy of an engine signal: it left 2.6 s after the tick, Saxo took it in 260 ms and WATA saw it 1.64 s later. */
export const BUY = {
  order_id: '7001',
  buy_sell: 'Buy',
  position_id: '5001',
  order_time: '2026-11-23T14:31:03Z',
  purpose: 'open',
  sent_at: '2026-11-23T14:31:03.020Z',
  accepted_at: '2026-11-23T14:31:03.280Z',
  confirmed_at: '2026-11-23T14:31:04.920Z',
  gave_up_at: null,
  lookups: 2,
  outcome: 'confirmed',
  cancel_accepted: null,
  detail: null,
  position: { position_id: '5001', position_status: 'Open', execution_time_open: '2026-11-23T14:31:03.270Z' },
  signal: { signal_uuid: UUID, signal_timestamp: '2026-11-23T14:31:00Z', received_timestamp: '2026-11-23T14:31:01.070Z', decided_at: null },
} as unknown as Order

export const SALE = {
  ...BUY,
  order_id: '7002',
  buy_sell: 'Sell',
  position_id: '4001',
  purpose: 'close',
  sent_at: '2026-11-23T14:31:02.100Z',
  accepted_at: '2026-11-23T14:31:02.350Z',
  confirmed_at: '2026-11-23T14:31:02.900Z',
  position: { position_id: '4001', position_status: 'Closed', execution_time_close: '2026-11-23T14:31:02.340Z' },
  signal: null,
} as unknown as Order

export function request(id: string, at: string, outcome: 'accepted' | 'refused', extra: Partial<OrderRequest> = {}): OrderRequest {
  return {
    request_id: id,
    requested_at: at,
    answered_at: at,
    operation: 'place',
    purpose: 'open',
    outcome,
    http_status: outcome === 'refused' ? 400 : 201,
    error_code: outcome === 'refused' ? 'InsufficientFunds' : null,
    error_message: outcome === 'refused' ? 'Not enough cash' : null,
    ...extra,
  } as OrderRequest
}

export const TRACE: SignalTrace = {
  ref: UUID,
  signal_uuid: UUID,
  action: 'long',
  indice: 'us100',
  source: { kind: 'engine', label: 'Rule: Kernel', rule_id: '0123456789ab', rule_name: 'Kernel', mode: 'live' },
  fate: { key: 'executed', reason: null },
  engine: {
    id: 9,
    rule_id: '0123456789ab',
    rule_name: 'Kernel',
    script_id: 'aaaaaaaaaaaa',
    script_version: 1,
    asset: 'capital/US100',
    side: 'mid',
    bars: 'R60',
    alert_title: 'Enter Long',
    mode: 'live',
    bar_time: '2026-11-23T14:30:50Z',
    bar_closed_at: '2026-11-23T14:31:00Z',
    raised_at: '2026-11-23T14:31:00.420Z',
    sent_at: '2026-11-23T14:31:01.320Z',
    outcome: 'sent',
    detail: 'Sent at attempt 2: the web server had not taken it before',
    attempts: 2,
  } as SignalTrace['engine'],
  run: {
    tick_at: '2026-11-23T14:31:00.420Z',
    started_at: '2026-11-23T14:31:00.430Z',
    ended_at: '2026-11-23T14:31:00.620Z',
    done_at: '2026-11-23T14:31:00.940Z',
    bar_closed_at: '2026-11-23T14:31:00Z',
    outcome: 'ok',
    milliseconds: 190,
  },
  run_missing: null,
  journal: {
    signal_uuid: UUID,
    action: 'long',
    indice: 'us100',
    confidence: null,
    signal_timestamp: '2026-11-23T14:31:00Z',
    received_timestamp: '2026-11-23T14:31:01.070Z',
    decided_at: '2026-11-23T14:31:05.620Z',
    outcome: 'executed',
    reason: null,
    position_id: '5001',
    order_id: '7001',
    alert_timestamp: '2026-11-23T14:31:00Z',
  },
  orders: [BUY],
  requests: [
    request('r-1', '2026-11-23T14:31:02.500Z', 'refused', { signal_uuid: UUID }),
    request('r-2', '2026-11-23T14:31:03.020Z', 'accepted', { signal_uuid: UUID, order_id: '7001' }),
  ],
  sold_for_it: [{ position_id: '4001', requests: [request('r-0', '2026-11-23T14:31:02.100Z', 'accepted', { purpose: 'close', order_id: '7002' })], order: SALE }],
}
