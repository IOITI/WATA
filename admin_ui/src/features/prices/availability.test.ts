import { describe, expect, it } from 'vitest'

import type { InstrumentAvailability, PriceAvailability, PriceInstrument } from '@/lib/types'

import { availabilityOf, flagText, instrumentFacts, isFlagged, quotedShare } from './availability'

const PARIS = 'Europe/Paris'
const at = (clock: string, day = '2026-10-12') => `${day}T${clock}Z`

const TURBO = {
  id: 2, uic: 40000002, asset_type: 'WarrantOpenEndKnockOut', symbol: 'SG-NDX-L2', description: 'NASDAQ 100 Turbo Long 24150 SG',
  currency: 'EUR', role: 'turbo', indice: 'us100', underlying_uic: 1909050, direction: 'long', barrier: 24150, issuer: 'SG',
  last_t: Date.parse(at('10:29:50')) / 1000, last_bid: 8.45, last_ask: 8.5, volume: 15300,
} as PriceInstrument

/** Quoted from 08:00 to 22:00 in Paris, with nothing missing unless said. */
function recorded(missing: Partial<InstrumentAvailability> = {}): InstrumentAvailability {
  return {
    bars: 5040, updates: 12840, first_bar: at('06:00:00'), last_bar: at('19:59:50'), quoted_seconds: 50400,
    missing_seconds: 0, holes: 0, hole_seconds: 0, stops: [], stretches: [], stretch_count: 0, knocked_out_at: null,
    without_ask: 0, delayed_minutes: 0, issues: [], ...missing,
  }
}

function period(items: PriceAvailability['items'], to = '2026-10-12'): PriceAvailability {
  return { from: '2026-10-12', to, timezone: PARIS, max_days: 7, items }
}

describe('a Dukascopy history', () => {
  const HISTORY = {
    ...TURBO, ref: 'dukascopy/us100', id: null, uic: null, role: 'underlying', symbol: 'USATECHIDXUSD', currency: 'USD',
  } as PriceInstrument

  it('has no availability: it is worked out for the instruments of the recorder alone', () => {
    expect(availabilityOf(period({ null: recorded() } as PriceAvailability['items']), HISTORY)).toBeUndefined()
  })

  it('is described without a Saxo number it does not have', () => {
    const described = instrumentFacts(HISTORY, undefined, PARIS)

    expect(described.find((fact) => fact.label === 'Instrument')?.value).toBe('USATECHIDXUSD · USD')
  })
})

const facts = (item: InstrumentAvailability | undefined, instrument = TURBO) =>
  Object.fromEntries(
    instrumentFacts(instrument, item === undefined ? undefined : period({ 2: item }), PARIS).map((fact) => [fact.label, fact]),
  )

describe('what flags an instrument', () => {
  it('is a hole in its day or a stop, nothing else', () => {
    expect(isFlagged(recorded())).toBe(false)
    expect(isFlagged(recorded({ issues: ['holes'] }))).toBe(true)
    expect(isFlagged(recorded({ issues: ['stopped'] }))).toBe(true)
    // Counted and shown, not flagged.
    expect(isFlagged(recorded({ without_ask: 40, delayed_minutes: 15, knocked_out_at: at('09:00:00') }))).toBe(false)
    // Not known yet: no mark.
    expect(isFlagged(undefined)).toBe(false)
  })

  it('is looked up by the instrument, and unknown while nothing was answered or for a period too long', () => {
    expect(availabilityOf(period({ 2: recorded() }), TURBO)?.bars).toBe(5040)
    expect(availabilityOf(undefined, TURBO)).toBeUndefined()
    expect(availabilityOf(period(null), TURBO)).toBeUndefined()
    expect(availabilityOf(period({ 3: recorded() }), TURBO)).toBeUndefined()
  })

  it('says when, for a single hole', () => {
    const item = recorded({
      issues: ['holes'], holes: 1, hole_seconds: 720, missing_seconds: 720, stretch_count: 1,
      stretches: [{ start: at('09:00:00'), end: at('09:12:00'), seconds: 720, reason: 'unquoted' }],
    })

    expect(flagText(item, PARIS, false)).toBe('Missing data: no quote for 12 min from 11:00:00')
    // Over several days the time alone would not say which day.
    expect(flagText(item, PARIS, true)).toBe('Missing data: no quote for 12 min from 12 Oct, 11:00:00')
  })

  it('counts the holes when there are several, and names the stop beside them', () => {
    const item = recorded({
      issues: ['holes', 'stopped'], holes: 2, hole_seconds: 840, missing_seconds: 33240, stretch_count: 3,
      stops: [at('11:00:00')],
      stretches: [
        { start: at('07:00:00'), end: at('07:02:00'), seconds: 120, reason: 'unquoted' },
        { start: at('09:00:00'), end: at('09:12:00'), seconds: 720, reason: 'unquoted' },
        { start: at('11:00:00'), end: at('20:00:00'), seconds: 32400, reason: 'stopped' },
      ],
    })

    expect(flagText(item, PARIS, false)).toBe('Missing data: 2 holes in its quotes, no more quote after 13:00:00')
  })

  it('says of a turbo that stopped on several days how many', () => {
    const item = recorded({ issues: ['stopped'], stops: [at('11:00:00'), at('09:00:00', '2026-10-13'), at('09:00:00', '2026-10-14')] })

    expect(flagText(item, PARIS, true)).toBe('Missing data: no more quote after 12 Oct, 13:00:00, and on 2 other days')
    expect(flagText({ ...item, stops: item.stops.slice(0, 2) }, PARIS, true)).toMatch(/, and on one other day$/)
  })

  it('has nothing to say of an instrument that misses nothing', () => {
    expect(flagText(recorded(), PARIS, false)).toBe('')
  })
})

describe('the share of the time an instrument had a quote', () => {
  it('counts what is missing against what was quoted', () => {
    expect(quotedShare(recorded())).toBe(100)
    expect(quotedShare(recorded({ quoted_seconds: 300, missing_seconds: 100 }))).toBe(75)
    expect(quotedShare(recorded({ quoted_seconds: 0, missing_seconds: 0 }))).toBeNull()
  })

  it('is never rounded up to a hundred while something is missing', () => {
    const item = recorded({
      quoted_seconds: 50400, missing_seconds: 30, holes: 1, hole_seconds: 30, stretch_count: 1, issues: ['holes'],
      stretches: [{ start: at('09:00:00'), end: at('09:00:30'), seconds: 30, reason: 'unquoted' }],
    })

    expect(facts(item).Availability.value).toBe('99.9% · 30 s without a quote while other instruments had one')
  })
})

describe('what an opened row says', () => {
  it('gives the last quote with its spread and its time, the volume and what was recorded', () => {
    const said = facts(recorded())

    expect(said['Last price'].value).toBe('Bid 8.45 · Ask 8.50 · spread 0.05 at 12:29:50')
    expect(said.Volume.value).toBe('15,300')
    expect(said['Quote changes'].value).toBe('12,840')
    expect(said.Recorded.value).toBe('08:00:00 to 21:59:50 · 5,040 bars')
    expect(said.Availability).toEqual({ label: 'Availability', value: '100.0% · nothing missing between its bars' })
    expect(said.Instrument.value).toBe('SG-NDX-L2 · UIC 40000002 · EUR')
    expect(Object.keys(said)).toEqual(['Last price', 'Volume', 'Quote changes', 'Recorded', 'Availability', 'Instrument'])
  })

  it('gives one level for an index, which has no bid and ask of its own', () => {
    const index = { ...TURBO, role: 'underlying', last_bid: 25000.5, last_ask: 25000.5, volume: null } as PriceInstrument

    const said = facts(recorded(), index)

    expect(said['Last price'].value).toBe('25,000.50 at 12:29:50')
    expect(said.Volume.value).toBe('Not recorded')
  })

  it('shows a price under one euro with the decimals it is quoted with', () => {
    const cheap = { ...TURBO, last_bid: 0.855, last_ask: 0.87 } as PriceInstrument

    expect(facts(recorded(), cheap)['Last price'].value).toBe('Bid 0.855 · Ask 0.870 · spread 0.015 at 12:29:50')
  })

  it('says how much is missing, in how many holes, and which was the longest', () => {
    const item = recorded({
      bars: 4956, quoted_seconds: 49560, missing_seconds: 840, holes: 2, hole_seconds: 840, stretch_count: 2,
      issues: ['holes'],
      stretches: [
        { start: at('07:00:00'), end: at('07:02:00'), seconds: 120, reason: 'unquoted' },
        { start: at('09:00:00'), end: at('09:12:00'), seconds: 720, reason: 'unquoted' },
      ],
    })

    expect(facts(item).Availability).toEqual({
      label: 'Availability', value: '98.3% · 14 min without a quote while other instruments had one', warning: true,
    })
    expect(facts(item).Holes).toEqual({
      label: 'Holes', value: '2 holes in its quotes, 14 min in all, the longest 12 min from 11:00:00', warning: true,
    })
    expect(facts(item).Stopped).toBeUndefined()
  })

  it('says of a single hole how long it was and when', () => {
    const item = recorded({
      missing_seconds: 720, holes: 1, hole_seconds: 720, stretch_count: 1, issues: ['holes'],
      stretches: [{ start: at('09:00:00'), end: at('09:12:00'), seconds: 720, reason: 'unquoted' }],
    })

    expect(facts(item).Holes.value).toBe('No quote for 12 min from 11:00:00')
  })

  it('tells a stop from a probable knock-out', () => {
    // One stop, which the recorder's own gap cut in two stretches: still one stop, and no hole.
    const stopped = recorded({
      bars: 1800, quoted_seconds: 18000, issues: ['stopped'], stops: [at('11:00:00')], missing_seconds: 32280, stretch_count: 2,
      stretches: [
        { start: at('11:00:00'), end: at('12:00:00'), seconds: 3600, reason: 'stopped' },
        { start: at('12:02:00'), end: at('20:00:00'), seconds: 28680, reason: 'stopped' },
      ],
    })
    const knockedOut = recorded({ knocked_out_at: at('11:00:00') })

    expect(facts(stopped).Stopped).toEqual({
      label: 'Stopped', value: 'No more quote after 13:00:00, while other turbos were still quoted', warning: true,
    })
    // 35.79…%: rounded down, like every share here.
    expect(facts(stopped).Availability.value).toBe('35.7% · 8 h 58 min without a quote while other instruments had one')
    expect(facts(stopped).Holes).toBeUndefined()
    expect(facts(stopped)['Knocked out']).toBeUndefined()
    expect(facts(knockedOut)['Knocked out'].value).toBe(
      "Probably, around 13:00:00: its bid had fallen to a fraction of the day's highest",
    )
    expect(facts(knockedOut)['Knocked out'].warning).toBeUndefined()
    expect(facts(knockedOut).Stopped).toBeUndefined()
  })

  it('mentions a missing ask and late quotes only when there are some', () => {
    const said = facts(recorded({ without_ask: 40, delayed_minutes: 15 }))

    expect(said.Ask.value).toBe('Missing on 40 of its bars')
    expect(said.Delay.value).toBe('Quotes received up to 15 min late')
    expect(facts(recorded()).Ask).toBeUndefined()
  })

  it('gives the day with the time when the period is several days', () => {
    const several = instrumentFacts(TURBO, period({ 2: recorded({ last_bar: at('19:59:50', '2026-10-13') }) }, '2026-10-13'), PARIS)

    expect(several.find((fact) => fact.label === 'Recorded')?.value).toBe('12 Oct, 08:00:00 to 13 Oct, 21:59:50 · 5,040 bars')
  })

  it('keeps to the last quote and the volume while the rest is being read', () => {
    expect(Object.keys(facts(undefined))).toEqual(['Last price', 'Volume', 'Instrument'])
  })

  it('says so when the period is too long for what is missing to be worked out', () => {
    const said = instrumentFacts(TURBO, period(null, '2026-11-30'), PARIS)

    expect(said.find((fact) => fact.label === 'Availability')?.value).toBe('Not worked out for a period of more than 7 days')
  })

  it('has no last price for an instrument that came without one', () => {
    const bare = { ...TURBO, last_t: undefined, last_bid: undefined, last_ask: undefined } as PriceInstrument

    expect(facts(recorded(), bare)['Last price'].value).toBe('—')
  })
})
