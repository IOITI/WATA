/**
 * The menu of the interface, and where each page stands in it: the sidebar
 * lists it, and the trail of the top bar says it, from this one table.
 */

export type MenuSection = 'Trading' | 'Analysis' | 'Data' | 'Technical Analysis' | 'Operations'

export interface MenuEntry {
  id: string
  path: string
  label: string
  section: MenuSection
}

export const MENU_SECTIONS: MenuSection[] = ['Trading', 'Analysis', 'Data', 'Technical Analysis', 'Operations']

export const MENU: MenuEntry[] = [
  { id: 'dashboard', path: '/', label: 'Dashboard', section: 'Trading' },
  { id: 'positions', path: '/positions', label: 'Positions', section: 'Trading' },
  { id: 'orders', path: '/orders', label: 'Orders', section: 'Trading' },
  { id: 'signals', path: '/signals', label: 'Signals', section: 'Trading' },
  { id: 'watchlist', path: '/watchlist', label: 'Watchlist', section: 'Trading' },
  { id: 'performance', path: '/performance', label: 'Performance', section: 'Analysis' },
  { id: 'report', path: '/report', label: 'Report', section: 'Analysis' },
  { id: 'exits', path: '/exits', label: 'Exits', section: 'Analysis' },
  { id: 'timing', path: '/timing', label: 'Execution timing', section: 'Analysis' },
  { id: 'backtests', path: '/backtests', label: 'Backtests', section: 'Analysis' },
  { id: 'providers', path: '/data/providers', label: 'Providers', section: 'Data' },
  { id: 'assets', path: '/data/assets', label: 'Assets View', section: 'Data' },
  { id: 'ta-indicators', path: '/ta/indicators', label: 'Indicators', section: 'Technical Analysis' },
  { id: 'ta-charts', path: '/ta/charts', label: 'Charts', section: 'Technical Analysis' },
  // "Signal rules": the Trading section's Signals page sends signals by hand.
  { id: 'ta-signals', path: '/ta/signals', label: 'Signal rules', section: 'Technical Analysis' },
  { id: 'ta-monitor', path: '/ta/monitor', label: 'Engine monitor', section: 'Technical Analysis' },
  { id: 'system', path: '/system', label: 'System', section: 'Operations' },
  { id: 'logs', path: '/logs', label: 'Logs', section: 'Operations' },
  { id: 'audit', path: '/audit', label: 'Audit trail', section: 'Operations' },
  { id: 'config', path: '/config', label: 'Configuration', section: 'Operations' },
]

// The pages that take the whole width: an editor beside its test, a chart.
const WIDE_PAGES = [/^\/ta\/indicators\/[^/]+\/?$/, /^\/ta\/charts\/?$/]

/** A page without the width cap of the others. */
export function isWidePage(pathname: string): boolean {
  return WIDE_PAGES.some((pattern) => pattern.test(pathname))
}

/** A step of a trail: a link unless it is the page shown, or a section, which has no page. */
export interface Crumb {
  label: string
  href?: string
}

/**
 * What a page deeper than the menu says of itself (its PageHeader does): its
 * name, and the pages between its entry of the menu and itself.
 */
export interface PageName {
  title: string
  parents?: Crumb[]
}

export const PAGE_NOT_FOUND = 'Page not found'
/** The end of a deeper page's trail until the page has said its name. */
export const UNNAMED = '…'

function withoutTrailingSlash(pathname: string): string {
  return pathname.replace(/\/+$/, '') || '/'
}

/** The entry of the menu a page is under: its own, or the one of the page it was reached from. */
export function menuEntry(pathname: string): MenuEntry | undefined {
  const path = withoutTrailingSlash(pathname)
  return MENU.find((entry) => (entry.path === '/' ? path === '/' : path === entry.path || path.startsWith(`${entry.path}/`)))
}

/**
 * Where a page is, after Home: the section of its entry of the menu (not a
 * link: a section has no page of its own), then the entry; for a page deeper
 * than the menu, the entry becomes a link and the page adds its parents and
 * its name. The dashboard is Home itself: nothing comes after it.
 */
export function trailFor(pathname: string, page?: PageName | null): Crumb[] {
  const entry = menuEntry(pathname)
  if (!entry) return [{ label: page?.title ?? PAGE_NOT_FOUND }]
  if (entry.path === '/') return []
  if (withoutTrailingSlash(pathname) === entry.path) return [{ label: entry.section }, { label: entry.label }]
  return [
    { label: entry.section },
    { label: entry.label, href: entry.path },
    ...(page?.parents ?? []),
    { label: page?.title ?? UNNAMED },
  ]
}
