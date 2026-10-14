import { describe, expect, it } from 'vitest'

import { dayView, parseView, periodPresets, viewParams, withPeriod } from './view'

const DAY = '2026-10-12'
const view = (query: string) => parseView(new URLSearchParams(query), DAY)

describe('the view an address asks for', () => {
  it('is the default day, at ten seconds, on the bid, when nothing is said', () => {
    expect(view('')).toEqual({ from: DAY, to: DAY, interval: 10, range: null, side: 'bid' })
    expect(view('')).toEqual(dayView(DAY))
  })

  it('reads the period, the interval and the side', () => {
    expect(view('from=2026-10-06&to=2026-10-12&interval=300&side=ask')).toEqual({
      from: '2026-10-06', to: '2026-10-12', interval: 300, range: null, side: 'ask',
    })
  })

  it('reads range bars of a size, or of a size left to the server', () => {
    expect(view('range=0.6').range).toBe(0.6)
    expect(view('range=15').range).toBe(15)
    expect(view('range=auto').range).toBe('auto')
  })

  it.each(['range=', 'range=abc', 'range=0', 'range=-2', 'range=Infinity'])('shows bars of a fixed length for %s', (query) => {
    expect(view(query).range).toBeNull()
  })

  it('starts a longer period on bars that fit one chart', () => {
    expect(view('from=2026-10-06&to=2026-10-12').interval).toBe(60)
  })

  it('refuses an interval the period cannot be shown at, or that does not exist', () => {
    expect(view('from=2026-10-06&to=2026-10-12&interval=10').interval).toBe(60)
    expect(view('interval=45').interval).toBe(10)
    expect(view('interval=abc').interval).toBe(10)
  })

  it.each([
    'from=2026-10-12',
    'from=2026-10-12&to=2026-10-06',
    'from=yesterday&to=today',
    'from=2026-02-31&to=2026-03-02',
    'from=2026-13-01&to=2026-13-02',
  ])('falls back to the default day for a period that makes no sense: %s', (query) => {
    expect(view(query)).toMatchObject({ from: DAY, to: DAY })
  })
})

describe('the address of a view', () => {
  it('says nothing of what is the default', () => {
    expect(viewParams(view(''), DAY).toString()).toBe('')
    expect(viewParams(view('from=2026-10-06&to=2026-10-12'), DAY).toString()).toBe('from=2026-10-06&to=2026-10-12')
  })

  it('reads back as the same view', () => {
    const asked = view('from=2026-10-06&to=2026-10-12&interval=300&side=ask')

    expect(parseView(viewParams(asked, DAY), DAY)).toEqual(asked)
  })

  it('keeps a range as a price, and reads it back', () => {
    expect(viewParams(view('range=0.6'), DAY).toString()).toBe('range=0.6')
    expect(viewParams(view('range=auto'), DAY).toString()).toBe('range=auto')
    const asked = view('range=0.07&side=ask')
    expect(parseView(viewParams(asked, DAY), DAY)).toEqual(asked)
  })

  it('keeps the range when the period changes', () => {
    expect(withPeriod(view('range=0.6'), '2026-10-06', '2026-10-12').range).toBe(0.6)
  })
})

describe('changing the period', () => {
  it('keeps the bars when the new period can be shown with them', () => {
    expect(withPeriod(view('interval=300'), '2026-10-06', '2026-10-12').interval).toBe(300)
  })

  it('takes longer bars when it cannot', () => {
    expect(withPeriod(view(''), '2026-10-06', '2026-10-12').interval).toBe(60)
  })

  it('offers today, yesterday, the last seven days and the last thirty for an asset still recorded', () => {
    expect(periodPresets(DAY, DAY)).toEqual([
      { label: 'Today', from: '2026-10-12', to: '2026-10-12' },
      { label: 'Yesterday', from: '2026-10-11', to: '2026-10-11' },
      { label: '7 days', from: '2026-10-06', to: '2026-10-12' },
      { label: '30 days', from: '2026-09-13', to: '2026-10-12' },
    ])
  })

  it('counts back from the last day with data for an asset that is no longer recorded', () => {
    expect(periodPresets('2026-09-22', DAY)).toEqual([
      { label: 'Last day', from: '2026-09-22', to: '2026-09-22' },
      { label: 'Day before', from: '2026-09-21', to: '2026-09-21' },
      { label: '7 days', from: '2026-09-16', to: '2026-09-22' },
      { label: '30 days', from: '2026-08-24', to: '2026-09-22' },
    ])
  })
})
