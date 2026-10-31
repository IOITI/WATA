import type { Menu, MenuEntry } from 'ioiti-ui'

/**
 * The menu of the interface, and where each page stands in it: the sidebar
 * lists it (with its icons, AppShell.tsx), and the trail of the top bar says
 * it, from this one table. ioiti-ui's shell reads it (`ADMIN_MENU`): which
 * entry a page is under (`menuEntry`) and its trail (`trailFor`) are the
 * package's.
 */

/** The interface's name: in the sidebar, and at the end of every tab's title ("Orders · WATA Admin"). */
export const APP_NAME = 'WATA Admin'

export type MenuSection = 'Trading' | 'Analysis' | 'Data' | 'Technical Analysis' | 'AI agent' | 'Operations'

export const MENU_SECTIONS: MenuSection[] = ['Trading', 'Analysis', 'Data', 'Technical Analysis', 'AI agent', 'Operations']

export const MENU: MenuEntry<MenuSection>[] = [
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
  // Claude from these pages: its conversations (a conversation's page is under Activity), what it keeps between
  // them, what they made.
  { id: 'agent', path: '/agent', label: 'Activity', section: 'AI agent' },
  { id: 'agent-memory', path: '/agent/memory', label: 'Memory', section: 'AI agent' },
  { id: 'agent-artefacts', path: '/agent/artefacts', label: 'Artefacts', section: 'AI agent' },
  { id: 'agent-routines', path: '/agent/routines', label: 'Routines', section: 'AI agent' },
  { id: 'system', path: '/system', label: 'System', section: 'Operations' },
  { id: 'logs', path: '/logs', label: 'Logs', section: 'Operations' },
  { id: 'audit', path: '/audit', label: 'Audit trail', section: 'Operations' },
  { id: 'config', path: '/config', label: 'Configuration', section: 'Operations' },
]

/** The menu as ioiti-ui's shell takes it. */
export const ADMIN_MENU: Menu<MenuSection> = { sections: MENU_SECTIONS, entries: MENU }

/** The way home of the admin's 404s: the dashboard is its home page. */
export const DASHBOARD_HOME = { href: '/', label: 'Back to the dashboard' }

// The pages that take the whole width: an editor beside its test, a chart.
const WIDE_PAGES = [/^\/ta\/indicators\/[^/]+\/?$/, /^\/ta\/charts\/?$/]

/** A page without the width cap of the others. */
export function isWidePage(pathname: string): boolean {
  return WIDE_PAGES.some((pattern) => pattern.test(pathname))
}
