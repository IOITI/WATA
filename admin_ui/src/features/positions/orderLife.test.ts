import { describe, expect, it } from 'vitest'

import type { Order, OrderRequest, Refusal } from '@/lib/types'

import { entryFigures, orderLife, orderResult, purposeOf, tookMs } from './orderLife'

const POSITION = {
  position_id: '5098',
  position_status: 'Closed',
  position_amount: 120,
  position_open_price: 9.31,
  position_close_price: 9.52,
  position_profit_loss: 25.2,
  position_total_performance_percent: 2.26,
  execution_time_open: '2026-10-13T07:31:02.297Z',
  execution_time_close: '2026-10-13T08:02:11.270Z',
  position_close_reason: 'Performance (Trailing Stop)',
}

/** A buy order Saxo accepted in 190 ms, whose position WATA saw 330 ms later at its second look. */
const BUY = {
  order_id: '5012',
  buy_sell: 'Buy',
  position_id: '5098',
  order_time: '2026-10-13T07:31:02Z',
  purpose: 'open',
  sent_at: '2026-10-13T07:31:02.120Z',
  accepted_at: '2026-10-13T07:31:02.310Z',
  confirmed_at: '2026-10-13T07:31:02.640Z',
  gave_up_at: null,
  lookups: 2,
  outcome: 'confirmed',
  cancel_accepted: null,
  detail: null,
  position: POSITION,
  signal: { signal_uuid: 'uuid-1', signal_timestamp: null, received_timestamp: '2026-10-13T07:31:01.950Z', decided_at: null },
} as unknown as Order

const SELL = {
  ...BUY,
  order_id: '5013',
  buy_sell: 'Sell',
  purpose: 'close',
  sent_at: '2026-10-13T08:02:11.100Z',
  accepted_at: '2026-10-13T08:02:11.290Z',
  confirmed_at: '2026-10-13T08:02:11.600Z',
  lookups: 1,
  signal: null,
} as unknown as Order

/** Given up: no position 10.8 s after Saxo accepted it, the cancellation's answer left to each test. */
const GIVEN_UP = {
  ...BUY,
  position_id: null,
  position: null,
  confirmed_at: null,
  gave_up_at: '2026-10-13T07:31:13.110Z',
  lookups: 14,
  outcome: 'gave_up',
  cancel_accepted: true,
} as unknown as Order

/** As WATA wrote orders before it kept their moments. */
const OLD = {
  order_id: '4001',
  buy_sell: 'Buy',
  position_id: '5098',
  order_time: '2026-09-01T07:31:03Z',
  position: POSITION,
} as unknown as Order

function steps(order: Order) {
  return orderLife(order).steps.map((step) => [step.label, step.note])
}

describe('an order that gave a position', () => {
  it('goes from the signal to the position being seen, each step set against the one before', () => {
    expect(steps(BUY)).toEqual([
      ['Signal received', ''],
      ['Order sent', '170 ms after the signal'],
      ['Accepted by Saxo', '190 ms after it left'],
      ['Filled at Saxo', "Saxo's clock: 177 ms after the order left"],
      ['Seen by WATA', '330 ms after Saxo accepted the order, at the 2nd look'],
    ])
  })

  it("keeps Saxo's fill apart from the server's own moments", () => {
    const clocks = orderLife(BUY).steps.map((step) => step.clock)
    expect(clocks).toEqual(['wata', 'wata', 'wata', 'saxo', 'wata'])
  })

  it('took the time from leaving to being seen, on one clock', () => {
    expect(tookMs(BUY)).toBe(520)
    expect(orderLife(BUY).summary).toBe('WATA saw the position 520 ms after the order left.')
    expect(orderResult(BUY)).toBe('opened')
  })

  it('says so when Saxo dates the fill before the order left, instead of a negative wait', () => {
    const early = { ...BUY, position: { ...POSITION, execution_time_open: '2026-10-13T07:31:02.100Z' } } as Order
    expect(steps(early)[3]).toEqual([
      'Filled at Saxo',
      "Saxo's clock: 20 ms before the order left, so the two clocks differ by at least that",
    ])
  })

  it('starts at the order when the journal has no signal for it', () => {
    expect(steps({ ...BUY, signal: null } as Order).slice(0, 2)).toEqual([
      ['Order sent', ''],
      ['Accepted by Saxo', '190 ms after it left'],
    ])
  })

  it('writes a wait of seconds as seconds', () => {
    const slow = { ...BUY, confirmed_at: '2026-10-13T07:31:06.710Z', lookups: 7 } as Order
    expect(steps(slow)[4]).toEqual(['Seen by WATA', '4.4 s after Saxo accepted the order, at the 7th look'])
    expect(orderLife(slow).summary).toBe('WATA saw the position 4.6 s after the order left.')
  })

  it.each([
    [1, '1st'],
    [3, '3rd'],
    [11, '11th'],
    [12, '12th'],
    [21, '21st'],
    [22, '22nd'],
  ])('names look %i the %s', (lookups, name) => {
    expect(steps({ ...BUY, lookups } as Order)[4][1]).toContain(`at the ${name} look`)
  })
})

describe('an order that gave no position', () => {
  it('says how long and how often the trader looked, then what Saxo answered to the cancellation', () => {
    expect(steps(GIVEN_UP).slice(2)).toEqual([
      ['Accepted by Saxo', '190 ms after it left'],
      ['No position', 'after 10.8 s and 14 looks'],
      ['Cancellation', 'Accepted by Saxo'],
    ])
    expect(orderResult(GIVEN_UP)).toBe('no_position')
    expect(orderLife(GIVEN_UP).summary).toBe(
      'Saxo accepted the order and showed no position for 10.8 s. The trader cancelled it, which Saxo accepted.',
    )
  })

  it('has no fill to show', () => {
    expect(steps(GIVEN_UP).map(([label]) => label)).not.toContain('Filled at Saxo')
  })

  it("gives Saxo's own words when it refused the cancellation, and what that can mean", () => {
    const refused = { ...GIVEN_UP, cancel_accepted: false, detail: 'SaxoApiError: Order not found' } as Order
    const life = orderLife(refused)

    expect(life.steps.at(-1)).toMatchObject({
      label: 'Cancellation',
      note: 'Refused by Saxo: SaxoApiError: Order not found',
      tone: 'danger',
    })
    expect(life.summary).toContain('may have been filled after the trader stopped looking')
    expect(life.summary).toContain('check the account at Saxo')
  })

  it('took the time until the trader gave up', () => {
    expect(tookMs(GIVEN_UP)).toBe(10_990)
  })

  it('stopped by an error says which, and what became of the order', () => {
    const failed = {
      ...GIVEN_UP,
      outcome: 'error',
      detail: 'RuntimeError: Saxo is unreachable',
      cancel_accepted: true,
    } as Order
    const life = orderLife(failed)

    expect(life.result).toBe('error')
    expect(life.steps.at(-1)).toMatchObject({ label: 'Stopped by an error', note: 'RuntimeError: Saxo is unreachable' })
    expect(life.summary).toBe(
      'An error stopped the trader once Saxo had the order. The order was cancelled, which Saxo accepted.',
    )
  })

  it('accepted and never heard of again says that nothing was recorded', () => {
    const silent = { ...GIVEN_UP, outcome: null, gave_up_at: null, cancel_accepted: null } as Order
    const life = orderLife(silent)

    expect(life.result).toBe('not_recorded')
    expect(life.steps.at(-1)?.label).toBe('Outcome not recorded')
    expect(life.tookMs).toBeNull()
  })
})

describe('a sell order', () => {
  it("is followed to the position being seen closed, with Saxo's time for the closure", () => {
    expect(steps(SELL)).toEqual([
      ['Sell order sent', ''],
      ['Accepted by Saxo', '190 ms after it left'],
      ['Closed at Saxo', "Saxo's clock: 170 ms after the order left"],
      ['Seen closed by WATA', '310 ms after Saxo accepted the order, at the 1st look'],
    ])
    expect(orderResult(SELL)).toBe('closed')
    expect(orderLife(SELL).summary).toBe('WATA saw the position closed 500 ms after the sell order left.')
  })

  it('never shows a signal: a sale is the monitor’s decision', () => {
    const withSignal = { ...SELL, signal: BUY.signal } as Order
    expect(steps(withSignal)[0][0]).toBe('Sell order sent')
  })

  it('not listed among the closed positions in time says how the closure was learnt', () => {
    const unseen = {
      ...SELL,
      confirmed_at: null,
      gave_up_at: '2026-10-13T08:02:19.190Z',
      lookups: 7,
      outcome: 'gave_up',
      detail: "Not among Saxo's closed positions",
    } as Order
    const life = orderLife(unseen)

    expect(life.result).toBe('not_seen_closed')
    expect(life.steps.at(-1)).toMatchObject({
      label: 'Not seen closed',
      note: "after 7.9 s and 7 looks: Not among Saxo's closed positions",
      tone: 'warning',
    })
    expect(life.summary).toContain('The closure was recorded later')
  })

  it('not seen closed and still open in the database says that instead', () => {
    const unseen = {
      ...SELL,
      confirmed_at: null,
      gave_up_at: '2026-10-13T08:02:19.190Z',
      outcome: 'gave_up',
      position: { ...POSITION, position_status: 'Open', execution_time_close: null },
    } as Order
    const life = orderLife(unseen)

    expect(life.summary).toContain('The position is still recorded as open.')
    expect(life.steps.map((step) => step.label)).not.toContain('Closed at Saxo')
  })
})

describe('an order placed before its moments were kept', () => {
  it('has the fill at Saxo and the second WATA wrote it down, and says that is all', () => {
    const life = orderLife(OLD)

    expect(life.tracked).toBe(false)
    expect(steps(OLD)).toEqual([
      ['Filled at Saxo', "Saxo's clock"],
      ['Recorded by WATA', 'To the second, once the position was confirmed'],
    ])
    expect(life.summary).toContain('were not written down')
    expect(life.tookMs).toBeNull()
  })

  it('is a buy that opened its position: nothing else was written then', () => {
    expect(purposeOf(OLD)).toBe('open')
    expect(orderResult(OLD)).toBe('opened')
  })
})

describe('the figures of a period', () => {
  const took = (milliseconds: number, id: string) =>
    ({
      ...BUY,
      order_id: id,
      confirmed_at: new Date(Date.parse(BUY.sent_at as string) + milliseconds).toISOString(),
    }) as Order

  it('give the typical and the slowest entry, and count those that gave no position', () => {
    const figures = entryFigures([took(300, 'a'), took(4_600, 'b'), took(500, 'c'), GIVEN_UP])

    expect(figures).toEqual({ tracked: 4, typicalMs: 500, slowestMs: 4_600, slowestOrderId: 'b', withoutPosition: 1 })
  })

  it('take the middle of an even number of entries', () => {
    expect(entryFigures([took(300, 'a'), took(500, 'b')]).typicalMs).toBe(400)
  })

  it('leave out the sells and the orders whose moments were never kept', () => {
    expect(entryFigures([SELL, OLD])).toEqual({
      tracked: 0,
      typicalMs: null,
      slowestMs: null,
      slowestOrderId: null,
      withoutPosition: 0,
    })
  })

  it('count an entry stopped by an error among those without a position', () => {
    expect(entryFigures([{ ...GIVEN_UP, outcome: 'error' } as Order]).withoutPosition).toBe(1)
  })
})


describe('a sell order Saxo had refused before taking it', () => {
  const LIMIT_ONLY = 'À ce stade, seuls des ordres limites sont autorisés'
  const answer = {
    outcome: 'refused' as const,
    http_status: 400,
    error_code: 'OtherError',
    error_message: LIMIT_ONLY,
    count: 37,
    first_at: '2026-10-13T08:00:00.000Z',
    last_at: '2026-10-13T08:02:10.000Z',
  }
  const refusal = { run_id: 'r1', count: 37, first_at: answer.first_at, last_at: answer.last_at, answers: [answer] } as Refusal

  it('shows the refusals before the order left, and says so', () => {
    const life = orderLife(SELL, refusal)

    expect(life.steps.map((step) => step.label)).toEqual([
      'Refused by Saxo ×37',
      'Sell order sent',
      'Accepted by Saxo',
      'Closed at Saxo',
      'Seen closed by WATA',
    ])
    expect(life.steps[0]).toMatchObject({
      at: answer.first_at,
      tone: 'danger',
      note: `37 attempts over 2 min 10 s: “${LIMIT_ONLY}” (OtherError, HTTP 400)`,
    })
    expect(life.summary).toBe(
      'WATA saw the position closed 500 ms after the sell order left. Saxo had refused 37 sell orders of this position before taking this one.',
    )
  })

  it('shows the resting orders cancelled on the way to the sell', () => {
    const cancel = {
      request_id: 'c1',
      operation: 'cancel',
      purpose: 'close',
      order_id: 'stop-1',
      outcome: 'accepted',
      requested_at: '2026-10-13T08:02:11.050Z',
    } as OrderRequest
    const refusedCancel = { ...cancel, request_id: 'c2', outcome: 'refused', error_message: 'Order not found', http_status: 404, error_code: null } as OrderRequest

    expect(steps({ ...SELL, requests: [cancel] } as Order).slice(0, 2)).toEqual([
      ['Resting orders cancelled', 'Order stop-1'],
      ['Sell order sent', ''],
    ])
    expect(orderLife({ ...SELL, requests: [refusedCancel] } as Order).steps[0]).toMatchObject({
      label: 'Resting orders not cancelled',
      note: 'Order stop-1: “Order not found” (HTTP 404)',
      tone: 'warning',
    })
  })
})
