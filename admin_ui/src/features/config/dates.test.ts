import { describe, expect, it } from 'vitest'

import { fromConfigDate, sortConfigDates, toConfigDate } from './dates'

describe('config dates', () => {
  it('writes a date the way the config expects it', () => {
    expect(toConfigDate(new Date(2026, 0, 5))).toBe('05/01/2026')
    expect(toConfigDate(new Date(2026, 11, 25))).toBe('25/12/2026')
  })

  it('reads a config date back to the same day', () => {
    const date = fromConfigDate('25/12/2026')

    expect(date && toConfigDate(date)).toBe('25/12/2026')
  })

  it('rejects what is not a real DD/MM/YYYY date', () => {
    for (const text of ['2026-12-25', '25/12/26', '31/02/2026', '00/01/2026', '12/13/2026', '']) {
      expect(fromConfigDate(text)).toBeNull()
    }
  })

  it('sorts by calendar, not by text, and drops duplicates', () => {
    expect(sortConfigDates(['25/12/2026', '01/01/2027', '04/07/2026', '25/12/2026'])).toEqual([
      '04/07/2026',
      '25/12/2026',
      '01/01/2027',
    ])
  })

  it('keeps unreadable entries, after the real dates', () => {
    expect(sortConfigDates(['soon', '04/07/2026'])).toEqual(['04/07/2026', 'soon'])
  })
})
