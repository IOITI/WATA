import { describe, expect, it } from 'vitest'

import type { Order, OrderRequest, Refusal } from '@/lib/types'

import {
  answerText,
  orderRows,
  refusalBefore,
  refusalFigures,
  refusalSummary,
  requestLabel,
  requestTookMs,
  rowId,
  runLabel,
  runTookMs,
  saxoAnswer,
  sellRefusalMarker,
  sellRefusalSentence,
  sellRefusalsOf,
} from './orderRequests'

const TZ = 'Europe/Paris'
// What Saxo answered on 2026-10-14, refusing a market sell in a volatile market.
const LIMIT_ONLY = 'À ce stade, seuls des ordres limites sont autorisés'

/** A market sell Saxo refused at 14:02:11 Paris time, answering in 120 ms. */
const REFUSED: OrderRequest = {
  request_id: 'request-1',
  requested_at: '2026-10-14T12:02:11.000Z',
  answered_at: '2026-10-14T12:02:11.120Z',
  recorded_by: 'position_monitor',
  operation: 'place',
  purpose: 'close',
  position_id: '7779123400',
  signal_uuid: null,
  action: 'long',
  reason: 'Performance (Stoploss (-7%) hit at -7.4%)',
  instrument_uic: 61252187,
  asset_type: 'WarrantOpenEndKnockOut',
  instrument_name: 'TURBO LONG NASDAQ-100',
  instrument_symbol: 'SG-NDX-L1',
  instrument_currency: 'EUR',
  buy_sell: 'Sell',
  amount: 6,
  order_type: 'Market',
  price_seen: 1.23,
  outcome: 'refused',
  order_id: null,
  http_status: 400,
  error_code: 'OtherError',
  error_message: LIMIT_ONLY,
  request_body: '{"Uic": 61252187, "Amount": 6, "BuySell": "Sell"}',
  response_body: `{"ErrorInfo": {"ErrorCode": "OtherError", "Message": "${LIMIT_ONLY}"}}`,
}

/** 37 refusals over 2 min 19 s, then the sale by order 5012. */
const RUN: Refusal = {
  run_id: 'request-1',
  purpose: 'close',
  position_id: '7779123400',
  signal_uuid: null,
  action: 'long',
  reason: 'Performance (Stoploss (-7%) hit at -7.4%)',
  instrument_name: 'TURBO LONG NASDAQ-100',
  instrument_symbol: 'SG-NDX-L1',
  instrument_uic: 61252187,
  instrument_currency: 'EUR',
  asset_type: 'WarrantOpenEndKnockOut',
  buy_sell: 'Sell',
  order_amount: 6,
  order_type: 'Market',
  instrument_price: 1.23,
  first_at: '2026-10-14T12:02:11.000Z',
  last_at: '2026-10-14T12:04:29.880Z',
  answered_at: '2026-10-14T12:04:30.000Z',
  count: 37,
  outcome: 'refused',
  http_status: 400,
  error_code: 'OtherError',
  error_message: LIMIT_ONLY,
  recorded_by: ['position_monitor'],
  answers: [
    {
      outcome: 'refused',
      http_status: 400,
      error_code: 'OtherError',
      error_message: LIMIT_ONLY,
      count: 37,
      first_at: '2026-10-14T12:02:11.000Z',
      last_at: '2026-10-14T12:04:29.880Z',
    },
  ],
  requests: [REFUSED],
  requests_total: 37,
  followed_by: { order_id: '5012', requested_at: '2026-10-14T12:04:31.000Z' },
  position: null,
  signal: null,
}

describe("Saxo's answer", () => {
  it('keeps the message as Saxo wrote it, with its code and the HTTP status', () => {
    expect(answerText(REFUSED)).toBe(`“${LIMIT_ONLY}” (OtherError, HTTP 400)`)
    expect(saxoAnswer(REFUSED)).toBe(`“${LIMIT_ONLY}” (OtherError, HTTP 400)`)
  })

  it('says what was accepted, and that an unanswered order may have been taken all the same', () => {
    expect(saxoAnswer({ ...REFUSED, outcome: 'accepted', order_id: '5012' })).toBe('Accepted: order 5012')
    expect(saxoAnswer({ ...REFUSED, operation: 'cancel', outcome: 'accepted' })).toBe('Cancellation accepted')
    const unanswered = saxoAnswer({ ...REFUSED, outcome: 'no_answer', http_status: null, error_message: 'ApiRequestException: timed out' })
    expect(unanswered).toMatch(/^ApiRequestException: timed out\. Saxo may have taken the order all the same/)
    expect(answerText({ ...REFUSED, error_message: null })).toBe('No message (OtherError, HTTP 400)')
  })

  it('names the request and how long Saxo took to answer it', () => {
    expect(requestLabel(REFUSED)).toBe('Sell 6 · Market')
    expect(requestLabel({ ...REFUSED, operation: 'cancel', order_id: 'stop-1,tp-1' })).toBe('Cancel orders stop-1, tp-1')
    expect(requestLabel({ ...REFUSED, purpose: 'stop', order_type: 'StopIfTraded', amount: 98 })).toBe(
      'Protective stop: Sell 98 · StopIfTraded',
    )
    expect(requestTookMs(REFUSED)).toBe(120)
  })
})

describe('a run of refusals', () => {
  it('reads as one row: how many times, for how long, and what came after', () => {
    expect(runLabel(RUN)).toBe('Refused by Saxo ×37')
    expect(runLabel({ ...RUN, count: 1 })).toBe('Refused by Saxo')
    expect(runTookMs(RUN)).toBe(139_000)
    expect(refusalSummary(RUN, TZ)).toBe(
      `Saxo refused to sell 6 of TURBO LONG NASDAQ-100 37 times in 2 min 19 s, answering “${LIMIT_ONLY}” (OtherError, HTTP 400). ` +
        'The position was sold by order 5012 at 14:04:31.',
    )
  })

  it('says when the position is still open, or was closed another way', () => {
    const going = { ...RUN, followed_by: null }
    expect(refusalSummary(going, TZ)).toMatch(/ The position is still open\.$/)
    expect(refusalSummary({ ...going, position: { position_status: 'Closed' } as Refusal['position'] }, TZ)).toMatch(
      /The position was closed later/,
    )
  })

  it('says a refused buy opened nothing, and an unanswered one may have', () => {
    const buy = { ...RUN, purpose: 'open' as const, position_id: null, followed_by: null, count: 1, buy_sell: 'Buy' }
    expect(refusalSummary(buy, TZ)).toBe(
      `Saxo refused to buy 6 of TURBO LONG NASDAQ-100, answering “${LIMIT_ONLY}” (OtherError, HTTP 400). No position was opened.`,
    )
    const unanswered = { ...buy, outcome: 'no_answer' as const }
    expect(refusalSummary(unanswered, TZ)).toMatch(/^WATA got no answer from Saxo when it tried to buy .* Saxo may have taken the order/)
  })

  it('counts several answers rather than choosing one', () => {
    const answers = [...RUN.answers, { ...RUN.answers[0], error_code: 'TooFarFromMarket', count: 2 }]
    expect(refusalSummary({ ...RUN, answers }, TZ)).toMatch(/37 times in 2 min 19 s, with 2 different answers\./)
  })

  it('is merged with the orders, newest first, and found by the order that went through', () => {
    const sale = { order_id: '5012', sent_at: '2026-10-14T12:04:31.000Z', order_time: '2026-10-14T12:04:31Z' } as Order
    const earlier = { order_id: '5001', sent_at: '2026-10-14T07:31:02.120Z', order_time: '2026-10-14T07:31:02Z' } as Order

    const rows = orderRows([sale, earlier], [RUN])

    expect(rows.map(rowId)).toEqual(['5012', 'refused:request-1', '5001'])
    expect(rows[1].kind).toBe('refusal')
    expect(refusalBefore(sale, [RUN])).toBe(RUN)
    expect(refusalBefore(earlier, [RUN])).toBeNull()
  })

  it('adds up the attempts of a period', () => {
    const later = { ...RUN, run_id: 'request-90', count: 2, last_at: '2026-10-14T15:00:00.000Z' }
    expect(refusalFigures([RUN, later])).toEqual({ attempts: 39, runs: 2, latest: later })
    expect(refusalFigures([])).toEqual({ attempts: 0, runs: 0, latest: null })
  })
})

describe('a position whose sell Saxo does not take', () => {
  const requests = [
    { ...REFUSED, request_id: 'r1' },
    { ...REFUSED, request_id: 'r2', requested_at: '2026-10-14T12:04:29.880Z' },
    { ...REFUSED, request_id: 'r3', operation: 'cancel' as const, outcome: 'accepted' as const },
  ]

  it('adds up its refused sells, the cancellations aside', () => {
    expect(sellRefusalsOf(requests)).toEqual({
      count: 2,
      first_at: '2026-10-14T12:02:11.000Z',
      last_at: '2026-10-14T12:04:29.880Z',
      outcome: 'refused',
      http_status: 400,
      error_code: 'OtherError',
      error_message: LIMIT_ONLY,
    })
    expect(sellRefusalsOf([{ ...REFUSED, outcome: 'accepted' }])).toBeNull()
  })

  it('says so under its name and at the top of its details', () => {
    const refusals = sellRefusalsOf(requests)!
    expect(sellRefusalMarker(refusals, TZ)).toBe('Sell refused ×2 · 14:04:29')
    expect(sellRefusalSentence(refusals, TZ, true)).toBe(
      `Saxo refused to sell this position twice, from 14:02:11 to 14:04:29. Last answer: “${LIMIT_ONLY}” (OtherError, HTTP 400). It is still open.`,
    )
    expect(sellRefusalMarker({ ...refusals, count: 1, outcome: 'no_answer' }, TZ)).toBe('Sell not answered · 14:04:29')
  })
})
