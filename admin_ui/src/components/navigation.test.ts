import { describe, expect, it } from 'vitest'

import { isWidePage, MENU, MENU_SECTIONS, menuEntry, PAGE_NOT_FOUND, trailFor, UNNAMED } from './navigation'

describe('the menu', () => {
  it('names each page once, under one of its sections', () => {
    expect(new Set(MENU.map((entry) => entry.id)).size).toBe(MENU.length)
    expect(new Set(MENU.map((entry) => entry.path)).size).toBe(MENU.length)
    expect(MENU.every((entry) => MENU_SECTIONS.includes(entry.section))).toBe(true)
  })

  it('knows the entry a deeper page was reached from', () => {
    expect(menuEntry('/data/assets/saxo/2')?.id).toBe('assets')
    expect(menuEntry('/backtests/r1/days/2026-10-09')?.id).toBe('backtests')
    expect(menuEntry('/positions/')?.id).toBe('positions')
    // A page whose address only begins like another's is not under it.
    expect(menuEntry('/data/assetsandmore')).toBeUndefined()
    expect(menuEntry('/nothing')).toBeUndefined()
  })

  it('gives the whole width to the script editor and the charts, and to them only', () => {
    expect(isWidePage('/ta/indicators/0123456789ab')).toBe(true)
    expect(isWidePage('/ta/indicators/new')).toBe(true)
    expect(isWidePage('/ta/charts')).toBe(true)
    expect(isWidePage('/ta/indicators')).toBe(false)
    expect(isWidePage('/positions')).toBe(false)
    expect(menuEntry('/ta/indicators/0123456789ab')?.id).toBe('ta-indicators')
    expect(trailFor('/ta/charts')).toEqual([{ label: 'Technical Analysis' }, { label: 'Charts' }])
  })
})

describe('where a page is', () => {
  it('is Home alone on the dashboard', () => {
    expect(trailFor('/')).toEqual([])
  })

  it('is the section of a page of the menu, which is no link, then the page', () => {
    expect(trailFor('/positions')).toEqual([{ label: 'Trading' }, { label: 'Positions' }])
    expect(trailFor('/positions/')).toEqual([{ label: 'Trading' }, { label: 'Positions' }])
    expect(trailFor('/data/assets')).toEqual([{ label: 'Data' }, { label: 'Assets View' }])
    expect(trailFor('/data/providers')).toEqual([{ label: 'Data' }, { label: 'Providers' }])
    expect(trailFor('/config')).toEqual([{ label: 'Operations' }, { label: 'Configuration' }])
  })

  it('leads from a deeper page back to its page of the menu, and ends with the name it gives itself', () => {
    expect(trailFor('/data/assets/saxo/2', { title: 'Long 24,150 · SG' })).toEqual([
      { label: 'Data' }, { label: 'Assets View', href: '/data/assets' }, { label: 'Long 24,150 · SG' },
    ])
    expect(trailFor('/data/providers/dukascopy', { title: 'Dukascopy' })).toEqual([
      { label: 'Data' }, { label: 'Providers', href: '/data/providers' }, { label: 'Dukascopy' },
    ])
  })

  it('goes through the pages a deeper page names between', () => {
    const day = { title: '9 Oct 2026', parents: [{ label: 'Morning breakout', href: '/backtests/r1' }] }

    expect(trailFor('/backtests/r1/days/2026-10-09', day)).toEqual([
      { label: 'Analysis' },
      { label: 'Backtests', href: '/backtests' },
      { label: 'Morning breakout', href: '/backtests/r1' },
      { label: '9 Oct 2026' },
    ])
  })

  it('has an end that says nothing until a deeper page has given its name', () => {
    expect(trailFor('/backtests/r1')).toEqual([{ label: 'Analysis' }, { label: 'Backtests', href: '/backtests' }, { label: UNNAMED }])
  })

  it('says of an address that is no page that it is none', () => {
    expect(trailFor('/nothing')).toEqual([{ label: PAGE_NOT_FOUND }])
    expect(trailFor('/positions/nothing', { title: PAGE_NOT_FOUND })).toEqual([
      { label: 'Trading' }, { label: 'Positions', href: '/positions' }, { label: PAGE_NOT_FOUND },
    ])
  })

  it('reads the name a page of the menu gives itself nowhere: the menu names it', () => {
    expect(trailFor('/timing', { title: 'Timing of executions' })).toEqual([{ label: 'Analysis' }, { label: 'Execution timing' }])
  })
})
