import { describe, expect, it } from 'vitest'

import { parseView, periodPresets, viewParams, withPeriod } from './view'

const TODAY = '2026-10-12'
const view = (query: string) => parseView(new URLSearchParams(query), TODAY)

describe('the view an address asks for', () => {
  it('is today, at ten seconds, on the bid, when nothing is said', () => {
    expect(view('')).toEqual({ instrument: null, from: TODAY, to: TODAY, interval: 10, range: null, side: 'bid' })
  })

  it('reads the instrument, the period, the interval and the side', () => {
    expect(view('instrument=4&from=2026-10-06&to=2026-10-12&interval=300&side=ask')).toEqual({
      instrument: 4, from: '2026-10-06', to: '2026-10-12', interval: 300, range: null, side: 'ask',
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
  ])('falls back to today for a period that makes no sense: %s', (query) => {
    expect(view(query)).toMatchObject({ from: TODAY, to: TODAY })
  })

  it.each(['instrument=abc', 'instrument=-3', 'instrument=1.5', 'instrument=0'])(
    'has no instrument for %s',
    (query) => {
      expect(view(query).instrument).toBeNull()
    },
  )
})

describe('the address of a view', () => {
  it('says nothing of what is the default', () => {
    expect(viewParams(view(''), TODAY).toString()).toBe('')
    expect(viewParams(view('from=2026-10-06&to=2026-10-12'), TODAY).toString()).toBe('from=2026-10-06&to=2026-10-12')
  })

  it('reads back as the same view', () => {
    const asked = view('instrument=4&from=2026-10-06&to=2026-10-12&interval=300&side=ask')

    expect(parseView(viewParams(asked, TODAY), TODAY)).toEqual(asked)
  })

  it('keeps a range as a price, and reads it back', () => {
    expect(viewParams(view('range=0.6'), TODAY).toString()).toBe('range=0.6')
    expect(viewParams(view('range=auto&instrument=2'), TODAY).toString()).toBe('instrument=2&range=auto')
    const asked = view('instrument=2&range=0.07&side=ask')
    expect(parseView(viewParams(asked, TODAY), TODAY)).toEqual(asked)
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

  it('offers today, yesterday and the last seven days', () => {
    expect(periodPresets(TODAY)).toEqual([
      { label: 'Today', from: '2026-10-12', to: '2026-10-12' },
      { label: 'Yesterday', from: '2026-10-11', to: '2026-10-11' },
      { label: '7 days', from: '2026-10-06', to: '2026-10-12' },
    ])
  })
})
