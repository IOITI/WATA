import { describe, expect, it } from 'vitest'

import type { JournalEntry, Order, OrderRequest, Position, PositionDetail } from '@/lib/types'

import { CLOSE_GIVE_UP_MS, closeProgress, SIGNAL_GIVE_UP_MS, signalProgress, type CloseAsked, type SignalSent } from './follow'

const ZONE = 'Europe/Paris'
const ASKED_AT = Date.parse('2026-10-23T13:00:00Z')

const ASKED: CloseAsked = { positionId: '5002', instrument: 'TURBO LONG NASDAQ-100 KO 24150 SG', amount: 260, askedAt: ASKED_AT }

const OPEN = {
  position_id: '5002',
  action: 'long',
  instrument_name: 'TURBO LONG NASDAQ-100 KO 24150 SG',
  instrument_currency: 'EUR',
  position_amount: 260,
  position_open_price: 8.42,
  position_close_price: null,
  position_profit_loss: null,
  position_total_performance_percent: null,
  position_status: 'Open',
  position_close_reason: null,
  execution_time_open: '2026-10-23T12:30:00Z',
  execution_time_close: null,
} as Position

const SOLD = {
  ...OPEN,
  position_status: 'Closed',
  position_close_price: 8.52,
  position_profit_loss: 26,
  position_total_performance_percent: 1.19,
  position_close_reason: 'Explicit Close (position 5002)',
  execution_time_close: '2026-10-23T13:00:03Z',
} as Position

function sell(at: string, outcome: OrderRequest['outcome'], extra: Partial<OrderRequest> = {}): OrderRequest {
  return {
    request_id: `r-${at}`,
    requested_at: at,
    answered_at: at,
    recorded_by: 'position_monitor',
    operation: 'place',
    purpose: 'close',
    position_id: '5002',
    signal_uuid: null,
    action: 'long',
    reason: 'Explicit Close (position 5002)',
    instrument_uic: 1,
    asset_type: 'WarrantKnockOut',
    instrument_name: OPEN.instrument_name,
    instrument_symbol: null,
    instrument_currency: 'EUR',
    buy_sell: 'Sell',
    amount: 260,
    order_type: 'Market',
    price_seen: 8.5,
    outcome,
    order_id: outcome === 'accepted' ? '8000001' : null,
    http_status: outcome === 'refused' ? 400 : null,
    error_code: outcome === 'refused' ? 'OtherError' : null,
    error_message: outcome === 'refused' ? 'Instrument not tradable' : null,
    request_body: null,
    response_body: null,
    ...extra,
  }
}

function detail(position: Position, requests: OrderRequest[] = [], orders: Order[] = []): PositionDetail {
  return { position, orders, requests }
}

describe('following a close', () => {
  it('says it was asked while no sell has left', () => {
    const state = closeProgress(ASKED, detail(OPEN), ASKED_AT + 5_000, ZONE)

    expect(state).toMatchObject({ status: 'processing', title: 'Close asked', final: false })
    expect(state.subject).toBe('260 × TURBO LONG NASDAQ-100 KO 24150 SG')
    expect(state.link?.href).toBe('/positions?find=5002')
  })

  it('waits for Saxo once it took the sell, and names the order', () => {
    const state = closeProgress(ASKED, detail(OPEN, [sell('2026-10-23T13:00:01Z', 'accepted')]), ASKED_AT + 2_000, ZONE)

    expect(state).toMatchObject({ status: 'verifying', title: 'Saxo took the sell', final: false })
    expect(state.reference).toEqual({ label: 'Order', value: '8000001' })
    expect(state.figures).toContainEqual({ label: 'Bid it was sent on', value: '€8.50' })
  })

  it('ends with the sale and its result', () => {
    const state = closeProgress(ASKED, detail(SOLD, [sell('2026-10-23T13:00:01Z', 'accepted')]), ASKED_AT + 4_000, ZONE)

    expect(state).toMatchObject({ status: 'success', title: 'Sold', final: true })
    expect(state.description).toBe('The position monitor sold it at market.')
    expect(state.figures).toContainEqual({ label: 'Sold at', value: '€8.52' })
    expect(state.figures).toContainEqual({ label: 'Result', value: '+€26.00 (+1.19%)' })
  })

  it('says so when something else closed it first', () => {
    const state = closeProgress(ASKED, detail({ ...SOLD, position_close_reason: 'Trailing stop' }), ASKED_AT + 4_000, ZONE)

    expect(state.description).toBe('It was closed by something else first: Trailing stop.')
  })

  it('gives Saxo’s answer when it refused the sell, and keeps watching for another attempt', () => {
    const state = closeProgress(
      ASKED,
      detail(OPEN, [sell('2026-10-23T13:00:01Z', 'refused'), sell('2026-10-23T13:00:06Z', 'refused')]),
      ASKED_AT + 8_000,
      ZONE,
    )

    expect(state).toMatchObject({ status: 'failed', title: 'Saxo refused the sell', final: false })
    expect(state.description).toBe('“Instrument not tradable” (OtherError, HTTP 400). The position is still open.')
    expect(state.figures).toContainEqual({ label: 'Attempts', value: '2' })
  })

  it('leaves out the sells sent before the ask', () => {
    const state = closeProgress(ASKED, detail(OPEN, [sell('2026-10-23T12:58:00Z', 'refused')]), ASKED_AT + 5_000, ZONE)

    expect(state.status).toBe('processing')
  })

  it('stops once the monitor would have dropped the request', () => {
    const state = closeProgress(ASKED, detail(OPEN), ASKED_AT + CLOSE_GIVE_UP_MS, ZONE)

    expect(state).toMatchObject({ status: 'unknown', title: 'No sell seen', final: true })
  })
})

const SENT: SignalSent = { signalUuid: '6b0f2a51-0000-4000-8000-000000000001', action: 'long', indice: 'US100', sentAt: ASKED_AT }

function decided(outcome: string, extra: Partial<JournalEntry> = {}): JournalEntry {
  return {
    signal_uuid: SENT.signalUuid,
    action: 'long',
    indice: 'US100',
    confidence: null,
    signal_timestamp: '2026-10-23T13:00:00Z',
    received_timestamp: '2026-10-23T13:00:00.200Z',
    decided_at: '2026-10-23T13:00:01.400Z',
    outcome,
    reason: null,
    position_id: null,
    order_id: null,
    ...extra,
  }
}

describe('following a signal sent by hand', () => {
  it('waits for the trader’s decision', () => {
    const state = signalProgress(SENT, undefined, undefined, ASKED_AT + 3_000, ZONE)

    expect(state).toMatchObject({ status: 'processing', title: 'Signal sent', subject: 'Long signal · US100', final: false })
  })

  it('reads the position it bought', () => {
    const entry = decided('executed', { position_id: '5003' })

    expect(signalProgress(SENT, entry, undefined, ASKED_AT + 2_000, ZONE)).toMatchObject({ status: 'verifying', final: false })
    const state = signalProgress(SENT, entry, { ...OPEN, position_id: '5003' }, ASKED_AT + 3_000, ZONE)
    expect(state).toMatchObject({ status: 'success', title: 'Bought', final: true })
    expect(state.figures).toContainEqual({ label: 'Received to decided', value: '1.2 s' })
    expect(state.figures).toContainEqual({ label: 'Bought at', value: '€8.42' })
    expect(state.link?.href).toBe('/positions?find=5003')
  })

  it('gives the rule that refused it', () => {
    const state = signalProgress(
      SENT,
      decided('refused', { reason: 'Breaking trading rule: Cooldown active after last loss.' }),
      undefined,
      ASKED_AT + 2_000,
      ZONE,
    )

    expect(state).toMatchObject({ status: 'failed', title: 'Refused by the trader', final: true })
    expect(state.description).toBe('Breaking trading rule: Cooldown active after last loss.')
  })

  it('says nothing came after a minute, and why that can be', () => {
    const state = signalProgress(SENT, undefined, undefined, ASKED_AT + SIGNAL_GIVE_UP_MS, ZONE)

    expect(state).toMatchObject({ status: 'unknown', title: 'No decision seen', final: true })
    expect(state.description).toContain('A newer signal for US100 may have replaced it')
  })
})
