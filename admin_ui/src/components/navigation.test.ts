import { menuEntry, PAGE_NOT_FOUND, sidebarSections, trailFor, UNNAMED } from 'ioiti-ui'
import { describe, expect, it } from 'vitest'

import { ADMIN_MENU, isWidePage, MENU, MENU_SECTIONS } from './navigation'

describe('the menu', () => {
  it('names each page once, under one of its sections', () => {
    expect(new Set(MENU.map((entry) => entry.id)).size).toBe(MENU.length)
    expect(new Set(MENU.map((entry) => entry.path)).size).toBe(MENU.length)
    expect(MENU.every((entry) => MENU_SECTIONS.includes(entry.section))).toBe(true)
  })

  it('knows the entry a deeper page was reached from', () => {
    expect(menuEntry(ADMIN_MENU, '/data/assets/saxo/2')?.id).toBe('assets')
    expect(menuEntry(ADMIN_MENU, '/backtests/r1/days/2026-10-09')?.id).toBe('backtests')
    expect(menuEntry(ADMIN_MENU, '/positions/')?.id).toBe('positions')
    // A page whose address only begins like another's is not under it.
    expect(menuEntry(ADMIN_MENU, '/data/assetsandmore')).toBeUndefined()
    expect(menuEntry(ADMIN_MENU, '/nothing')).toBeUndefined()
  })

  it('gives the whole width to the script editor and the charts, and to them only', () => {
    expect(isWidePage('/ta/indicators/0123456789ab')).toBe(true)
    expect(isWidePage('/ta/indicators/new')).toBe(true)
    expect(isWidePage('/ta/charts')).toBe(true)
    expect(isWidePage('/ta/indicators')).toBe(false)
    expect(isWidePage('/positions')).toBe(false)
    expect(menuEntry(ADMIN_MENU, '/ta/indicators/0123456789ab')?.id).toBe('ta-indicators')
    expect(trailFor(ADMIN_MENU, '/ta/charts')).toEqual([{ label: 'Technical Analysis' }, { label: 'Charts' }])
  })

  it('has the AI agent as one entry at the foot of the sidebar: its activity, a conversation under it, what it made under it', () => {
    expect(menuEntry(ADMIN_MENU, '/agent')?.id).toBe('agent')
    expect(menuEntry(ADMIN_MENU, '/agent/20261028-080000-a1b2c3')?.id).toBe('agent')
    expect(menuEntry(ADMIN_MENU, '/agent/artefacts')?.id).toBe('agent-artefacts')
    expect(trailFor(ADMIN_MENU, '/agent')).toEqual([{ label: 'AI agent' }])
    expect(trailFor(ADMIN_MENU, '/agent/artefacts')).toEqual([{ label: 'AI agent', href: '/agent' }, { label: 'Artefacts' }])
    expect(trailFor(ADMIN_MENU, '/agent/20261028-080000-a1b2c3', { title: 'Analyse this run' })).toEqual([
      { label: 'AI agent', href: '/agent' }, { label: 'Analyse this run' },
    ])
  })

  it('has the Engine monitor under the Signal rules, and the LuxAlgo Library under the Indicators', () => {
    expect(menuEntry(ADMIN_MENU, '/ta/monitor')?.id).toBe('ta-monitor')
    expect(menuEntry(ADMIN_MENU, '/ta/signals')?.id).toBe('ta-signals')
    expect(trailFor(ADMIN_MENU, '/ta/monitor')).toEqual([
      { label: 'Technical Analysis' }, { label: 'Signal rules', href: '/ta/signals' }, { label: 'Engine monitor' },
    ])
    expect(trailFor(ADMIN_MENU, '/ta/luxalgo/indicators/smc', { title: 'Smart Money Concepts' })).toEqual([
      { label: 'Technical Analysis' },
      { label: 'Indicators', href: '/ta/indicators' },
      { label: 'LuxAlgo Library', href: '/ta/luxalgo' },
      { label: 'Smart Money Concepts' },
    ])
  })

  it('has the Disk, the Logs and the Audit trail under the System, and Paper trading under the Backtests', () => {
    expect(trailFor(ADMIN_MENU, '/system/disk')).toEqual([{ label: 'Operations' }, { label: 'System', href: '/system' }, { label: 'Disk' }])
    expect(trailFor(ADMIN_MENU, '/logs')).toEqual([{ label: 'Operations' }, { label: 'System', href: '/system' }, { label: 'Logs' }])
    expect(trailFor(ADMIN_MENU, '/audit')).toEqual([{ label: 'Operations' }, { label: 'System', href: '/system' }, { label: 'Audit trail' }])
    expect(trailFor(ADMIN_MENU, '/backtests/paper/live', { title: 'Paper report' })).toEqual([
      { label: 'Analysis' },
      { label: 'Backtests', href: '/backtests' },
      { label: 'Paper trading', href: '/backtests/paper' },
      { label: 'Paper report' },
    ])
    // A run is still a backtest's page, a paper run's too.
    expect(menuEntry(ADMIN_MENU, '/backtests/20261012-090000-abcdef')?.id).toBe('backtests')
  })

  it('folds every section but Trading in the sidebar, each entry under its own', () => {
    const sections = sidebarSections(ADMIN_MENU)
    expect(sections.filter((section) => section.foldable).map((section) => section.id)).toEqual(['Analysis', 'Data', 'Technical Analysis', 'Operations'])
    expect(sections.find((section) => section.id === 'AI agent')).toMatchObject({ place: 'footer', title: undefined })
    const children = (id: string) =>
      sections.flatMap((section) => section.items).find((item) => item.id === id)?.children?.map((child) => child.id)
    expect(children('system')).toEqual(['disk', 'logs', 'audit'])
    expect(children('agent')).toEqual(['agent-memory', 'agent-artefacts', 'agent-routines'])
    expect(children('backtests')).toEqual(['paper'])
    expect(children('ta-signals')).toEqual(['ta-monitor'])
    expect(children('ta-indicators')).toEqual(['ta-luxalgo'])
  })
})

describe('where a page is', () => {
  it('is Home alone on the dashboard', () => {
    expect(trailFor(ADMIN_MENU, '/')).toEqual([])
  })

  it('is the section of a page of the menu, which is no link, then the page', () => {
    expect(trailFor(ADMIN_MENU, '/positions')).toEqual([{ label: 'Trading' }, { label: 'Positions' }])
    expect(trailFor(ADMIN_MENU, '/positions/')).toEqual([{ label: 'Trading' }, { label: 'Positions' }])
    expect(trailFor(ADMIN_MENU, '/data/assets')).toEqual([{ label: 'Data' }, { label: 'Assets View' }])
    expect(trailFor(ADMIN_MENU, '/data/providers')).toEqual([{ label: 'Data' }, { label: 'Providers' }])
    expect(trailFor(ADMIN_MENU, '/config')).toEqual([{ label: 'Operations' }, { label: 'Configuration' }])
  })

  it('leads from a deeper page back to its page of the menu, and ends with the name it gives itself', () => {
    expect(trailFor(ADMIN_MENU, '/data/assets/saxo/2', { title: 'Long 24,150 · SG' })).toEqual([
      { label: 'Data' }, { label: 'Assets View', href: '/data/assets' }, { label: 'Long 24,150 · SG' },
    ])
    expect(trailFor(ADMIN_MENU, '/data/providers/dukascopy', { title: 'Dukascopy' })).toEqual([
      { label: 'Data' }, { label: 'Providers', href: '/data/providers' }, { label: 'Dukascopy' },
    ])
  })

  it('goes through the pages a deeper page names between', () => {
    const day = { title: '9 Oct 2026', parents: [{ label: 'Morning breakout', href: '/backtests/r1' }] }

    expect(trailFor(ADMIN_MENU, '/backtests/r1/days/2026-10-09', day)).toEqual([
      { label: 'Analysis' },
      { label: 'Backtests', href: '/backtests' },
      { label: 'Morning breakout', href: '/backtests/r1' },
      { label: '9 Oct 2026' },
    ])
  })

  it('has an end that says nothing until a deeper page has given its name', () => {
    expect(trailFor(ADMIN_MENU, '/backtests/r1')).toEqual([{ label: 'Analysis' }, { label: 'Backtests', href: '/backtests' }, { label: UNNAMED }])
  })

  it('says of an address that is no page that it is none', () => {
    expect(trailFor(ADMIN_MENU, '/nothing')).toEqual([{ label: PAGE_NOT_FOUND }])
    expect(trailFor(ADMIN_MENU, '/positions/nothing', { title: PAGE_NOT_FOUND })).toEqual([
      { label: 'Trading' }, { label: 'Positions', href: '/positions' }, { label: PAGE_NOT_FOUND },
    ])
  })

  it('reads the name a page of the menu gives itself nowhere: the menu names it', () => {
    expect(trailFor(ADMIN_MENU, '/timing', { title: 'Timing of executions' })).toEqual([{ label: 'Analysis' }, { label: 'Execution timing' }])
  })
})
